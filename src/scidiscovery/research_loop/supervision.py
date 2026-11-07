"""Pure host policy and projections for explicit scientific exploration rounds.

The accepted-search publisher owns this small cumulative value. Original round
facts remain in its existing immutable index; neither agents nor this module
write a second ledger or query an execution backend.
"""
from __future__ import annotations

import copy
import math

DEFAULT_POLICY = dict(version=1, consecutive_rounds=4, min_budget_fraction=0.10,
    budget_checkpoints=[0.30, 0.65], max_automatic_reviews=2)
_UNOBSERVED = object()


def policy(value=None):
    value = copy.deepcopy(DEFAULT_POLICY if value is None else value)
    if not isinstance(value, dict) or set(value) != set(DEFAULT_POLICY):
        raise ValueError('Exact scientific supervision policy required')
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Unsupported scientific supervision policy')
    for field in ('consecutive_rounds', 'max_automatic_reviews'):
        if type(value[field]) is not int or value[field] < (1 if field == 'consecutive_rounds' else 0):
            raise ValueError('Finite scientific supervision counts required')
    fraction = _fraction(value['min_budget_fraction'])
    points = value['budget_checkpoints']
    if (fraction is None or fraction > 1 or not isinstance(points, list)
            or any(_fraction(point) is None or not 0 < point <= 1 for point in points)
            or points != sorted(set(points))):
        raise ValueError('Ordered finite scientific supervision fractions required')
    return value


def _fraction(value):
    if type(value) not in (int,float):
        return None
    try:
        numeric=float(value)
    except OverflowError:
        return None
    return numeric if math.isfinite(numeric) and numeric>=0 else None


def empty(value=None):
    return dict(version=1, policy=policy(value), direction=None, consecutive_rounds=0,
        total_rounds=0, covered_rounds=0, last_round=None, last_covered_cutoff=None,
        automatic_reviews=0, last_review_fraction=None, max_budget_fraction=None,
        covered_checkpoints=[], pending_due=[], count_due_round=None, recent_rounds=[], diagnostics=[])


def _state(value, selected_policy=None):
    if isinstance(value,dict) and 'table' in value and 'node_count' in value and 'supervision' not in value:
        if type(value['node_count']) is int and value['node_count']==0:
            return empty(selected_policy)
        raise ValueError('Accepted scientific summary omitted its supervision ledger')
    initial=isinstance(value,dict) and value.get('node_count')==0
    value = value.get('supervision', value) if isinstance(value, dict) else None
    if value is None:
        return empty(selected_policy)
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('Accepted scientific supervision state required')
    result = copy.deepcopy(value)
    frozen = policy(result['policy'])
    if selected_policy is not None and policy(selected_policy) != frozen:
        if initial:
            return empty(selected_policy)
        raise ValueError('Scientific supervision policy cannot reset accepted counters')
    return result


def cutoff(value):
    state = _state(value)
    last = state['last_round'] or {}
    return dict(total_rounds=state['total_rounds'], source_step=last.get('source_step'),
        round_id=last.get('round_id'))


def observe(value, budget_fraction=None, *, selected_policy=None):
    """Retain crossed triggers even when later observations are unavailable."""
    state = _state(value, selected_policy)
    chosen = state['policy']
    fraction = _fraction(budget_fraction)
    if fraction is not None:
        previous = state['max_budget_fraction']
        state['max_budget_fraction'] = fraction if previous is None else max(previous, fraction)
    maximum = state['max_budget_fraction']
    pending = state['pending_due']
    if state['consecutive_rounds'] >= chosen['consecutive_rounds']:
        if 'consecutive_rounds' not in pending:
            pending.append('consecutive_rounds')
            state['count_due_round'] = state['total_rounds']
    if maximum is not None:
        for point in chosen['budget_checkpoints']:
            reason = 'budget_checkpoint:' + repr(point)
            if maximum >= point and point not in state['covered_checkpoints'] and reason not in pending:
                pending.append(reason)
    return state


def due(value, budget_fraction=None, policy=None):
    state = observe(value, budget_fraction, selected_policy=policy)
    fraction = state['max_budget_fraction']
    last = state['last_review_fraction']
    spacing=state['policy']['min_budget_fraction']
    reviewed=state['last_covered_cutoff'] is not None
    cooldown = reviewed and spacing>0 and (last is None or fraction is None
        or fraction - last < spacing - 1e-12)
    allowed = state['automatic_reviews'] < state['policy']['max_automatic_reviews']
    return dict(due=bool(state['pending_due']), reasons=copy.deepcopy(state['pending_due']),
        cutoff=cutoff(state), automatic_allowed=bool(state['pending_due']) and allowed and not cooldown,
        cooldown=cooldown, automatic_reviews=state['automatic_reviews'], budget_fraction=fraction,
        current_budget_fraction=_fraction(budget_fraction))


def request(value, *, budget_fraction=None, policy=None, automatic=False, final=False):
    state = observe(value, budget_fraction, selected_policy=policy)
    status = due(state, budget_fraction)
    return dict(version=1, cutoff=status['cutoff'], due_reasons=status['reasons'],
        budget_fraction=status['budget_fraction'], automatic=bool(automatic), final=bool(final))


def worker_review(job, intent, *, summary, budget_fraction, selected_policy):
    """Project an actionable direction checkpoint from one accepted/current cut.

    A running W cannot publish accepted scientific rounds. Budget checkpoints
    therefore also bound the first long W without guessing progress from calls,
    scores, elapsed node targets or native context events. Only the ordinary
    post-return controller can freeze the returned W's scientific review input.
    """
    if (selected_policy is None or summary is None or job.get('role') != 'W'
            or intent.get('output_repair') or intent.get('work_repair')
            or intent.get('continuation_kind') == 'author_closeout'
            or (intent.get('budget_slice') or {}).get('final_return') is not None
            or job.get('formal_review') or job.get('engineering_repair')
            or job.get('purpose') in {'closeout', 'formal_review', 'engineering_repair', 'worker_consultation'}
            or job.get('final_report') or job.get('final_only')):
        return None
    status = due(summary, budget_fraction=budget_fraction, policy=selected_policy)
    if not status['automatic_allowed']:
        return None
    return dict(reasons=status['reasons'], cutoff=status['cutoff'],
        budget_fraction=status['budget_fraction'])


def add_round(value, fact):
    state = _state(value)
    ref = fact['mechanism']
    state['consecutive_rounds'] = state['consecutive_rounds'] + 1 if state['direction'] == ref else 1
    state['direction'] = ref
    state['total_rounds'] += 1
    state['last_round'] = dict(round_id=fact['round_id'], source_step=fact['source_step'])
    # This bounded excerpt is navigation. The fact and its exact author pointer
    # retain complete text, call originals and immutable producer bindings.
    state['recent_rounds'] = (state['recent_rounds'] + [dict(round_id=fact['round_id'],
        source_step=fact['source_step'], pointer=fact['pointer'], mechanism=ref,
        question=fact['question'][:160], finding=fact['finding'][:240],
        question_truncated=len(fact['question']) > 160, finding_truncated=len(fact['finding']) > 240)])[-2:]
    return observe(state)


def cover(value, frozen, *, source_step, completion_fraction=_UNOBSERVED):
    """A meaningful accepted A covers its frozen boundary, never newer rounds."""
    state = observe(value,frozen.get('budget_fraction'))
    if completion_fraction is not _UNOBSERVED:
        state=observe(state,completion_fraction)
    boundary = frozen['cutoff']['total_rounds']
    if type(boundary) is not int or not state['covered_rounds'] <= boundary <= state['total_rounds']:
        raise ValueError('Scientific supervision cutoff moved outside accepted rounds')
    state['covered_rounds'] = boundary
    state['last_covered_cutoff'] = dict(copy.deepcopy(frozen['cutoff']), assessment_step=source_step)
    state['consecutive_rounds'] = min(state['consecutive_rounds'], state['total_rounds'] - boundary)
    known_completion=completion_fraction is _UNOBSERVED or _fraction(completion_fraction) is not None
    state['last_review_fraction']=state['max_budget_fraction'] if known_completion else None
    # The A can merge budget thresholds consumed during this same assessment
    # when it still covers every accepted round. Newer rounds retain their own
    # pending triggers and cannot be covered by an older frozen task.
    fraction = state['max_budget_fraction'] if boundary==state['total_rounds'] else _fraction(frozen.get('budget_fraction'))
    if fraction is not None:
        for point in state['policy']['budget_checkpoints']:
            if fraction >= point and point not in state['covered_checkpoints']:
                state['covered_checkpoints'].append(point)
    state['automatic_reviews'] += int(frozen.get('automatic') is True)
    count_covered = state['count_due_round'] is not None and state['count_due_round'] <= boundary
    if count_covered:
        state['count_due_round'] = None
    state['pending_due'] = [reason for reason in state['pending_due']
        if not (reason == 'consecutive_rounds' and count_covered) and not (reason.startswith('budget_checkpoint:')
            and float(reason.split(':', 1)[1]) in state['covered_checkpoints'])]
    return observe(state)


def project(value, *, budget_fraction=None):
    """Read-only scientific fields; private paths, bindings and tokens stay host-side."""
    state = observe(value, budget_fraction)
    status = due(state, budget_fraction)
    return dict(version=1, direction=state['direction'], consecutive_rounds=state['consecutive_rounds'],
        total_rounds=state['total_rounds'], covered_rounds=state['covered_rounds'],
        cutoff=cutoff(state), last_covered_cutoff=copy.deepcopy(state['last_covered_cutoff']),
        due_reasons=status['reasons'], automatic_reviews=state['automatic_reviews'],
        recent_rounds=copy.deepcopy(state['recent_rounds']),
        rounds_omitted_count=max(0, state['total_rounds'] - len(state['recent_rounds'])),
        diagnostics=copy.deepcopy(state['diagnostics'][:4]),
        diagnostics_omitted_count=state.get('diagnostics_omitted_count',0)+max(0, len(state['diagnostics']) - 4),
        scope='Explicit W scientific experiment batches with new exact managed originals; counts grant no scientific qualification.')
