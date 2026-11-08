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
WORKER_FIRST_POLICY = dict(version=2, mode='off', consecutive_calls=4,
    min_budget_fraction=0.10, budget_checkpoints=[], max_automatic_reviews=0)
_UNOBSERVED = object()


def policy(value=None):
    value = copy.deepcopy(DEFAULT_POLICY if value is None else value)
    if not isinstance(value, dict):
        raise ValueError('Exact scientific supervision policy required')
    modern=type(value.get('version')) is int and value['version']==2
    if set(value) != set(WORKER_FIRST_POLICY if modern else DEFAULT_POLICY):
        raise ValueError('Exact scientific supervision policy required')
    if type(value['version']) is not int or value['version'] not in (1,2):
        raise ValueError('Unsupported scientific supervision policy')
    if modern and value['mode'] not in {'off','notice','review'}:
        raise ValueError('Explicit investment supervision mode required')
    counter='consecutive_calls' if modern else 'consecutive_rounds'
    for field in (counter, 'max_automatic_reviews'):
        if type(value[field]) is not int or value[field] < (1 if field == counter else 0):
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
    selected=policy(value)
    result=dict(version=1, policy=selected, direction=None, consecutive_rounds=0,
        total_rounds=0, covered_rounds=0, last_round=None, last_covered_cutoff=None,
        automatic_reviews=0, last_review_fraction=None, max_budget_fraction=None,
        covered_checkpoints=[], pending_due=[], count_due_round=None, recent_rounds=[], diagnostics=[])
    if selected['version']==2:
        result.update(investment=dict(originals={},total_calls=0,consecutive_calls=0,
            mechanism_ref=None,covered_calls=0,count_due_call=None,lineage={}))
    return result


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
    if chosen['version']==2:
        if chosen['mode']=='off':return state
        investment=state['investment']
        if investment['consecutive_calls']>=chosen['consecutive_calls'] and 'consecutive_calls' not in pending:
            pending.append('consecutive_calls');investment['count_due_call']=investment['total_calls']
    elif state['consecutive_rounds'] >= chosen['consecutive_rounds']:
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
    allowed = (state['automatic_reviews'] < state['policy']['max_automatic_reviews']
        and (state['policy']['version']==1 or state['policy']['mode']=='review'))
    return dict(due=bool(state['pending_due']), reasons=copy.deepcopy(state['pending_due']),
        cutoff=cutoff(state), automatic_allowed=bool(state['pending_due']) and allowed and not cooldown,
        cooldown=cooldown, automatic_reviews=state['automatic_reviews'], budget_fraction=fraction,
        current_budget_fraction=_fraction(budget_fraction))


def add_investment(value,fact):
    """Count exact original calls once, without inventing scientific rounds."""
    state=_state(value)
    if state['policy']['version']!=2:return state
    state=declare_investment(state,fact)
    original=fact['original_call_id'];ref=fact['mechanism_ref']
    if not isinstance(original,str) or not original or not isinstance(ref,str) or not ref:
        raise ValueError('Exact original call and declared mechanism required')
    investment=state['investment'];known=investment['originals'].get(original)
    if known is not None:
        if known!=ref:raise ValueError('Original investment changed its declared identity')
        return state
    investment['originals'][original]=ref
    investment['total_calls']+=1
    related=_investment_ancestry(ref,investment['lineage'])
    investment['consecutive_calls']=(investment['consecutive_calls']+1
        if investment['mechanism_ref'] in related else 1)
    investment['mechanism_ref']=ref
    return observe(state)


def declare_investment(value,fact):
    """Retain declared ancestry for original-call unions, including no-call merges."""
    state=_state(value)
    if state['policy']['version']!=2:return state
    ref=fact['mechanism_ref'];predecessors=fact.get('predecessors',[])
    if (not isinstance(ref,str) or not ref or not isinstance(predecessors,list)
            or any(not isinstance(parent,str) or not parent or parent==ref for parent in predecessors)):
        raise ValueError('Exact investment predecessor identities required')
    lineage=state['investment']['lineage']
    lineage[ref]=sorted(set(lineage.get(ref,[]))|set(predecessors))
    return state


def investment_counts(value):
    """Per-identity investments are original-call set unions, never summed receipts."""
    state=_state(value)
    if state['policy']['version']!=2:return {}
    investment=state['investment'];lineage=investment['lineage']
    refs=set(lineage)|set(investment['originals'].values())|set(investment.get('live_calls',{}))
    result={}
    for ref in refs:
        related=_investment_ancestry(ref,lineage)
        result[ref]=(sum(original_ref in related for original_ref in investment['originals'].values())
            +sum(count for original_ref,count in investment.get('live_calls',{}).items() if original_ref in related))
    return result


def _investment_ancestry(ref,lineage):
    related=set();pending=[ref]
    while pending:
        current=pending.pop()
        if current in related:continue
        related.add(current);pending.extend(lineage.get(current,[]))
    return related


def with_live_investment(value,live,*,scope):
    """Merge one current task projection with accepted history, without reads."""
    state=_state(value)
    if state['policy']['version']!=2 or live is None:return state
    if (not isinstance(live,dict) or live.get('version')!=1 or live.get('scope')!=scope
            or type(live.get('total_calls')) is not int or live['total_calls']<0
            or type(live.get('consecutive_calls')) is not int
            or not 0<=live['consecutive_calls']<=live['total_calls']):
        raise ValueError('Exact current task investment required')
    import json
    prefix=json.dumps(scope['job_ref'],sort_keys=True,separators=(',',':'))+':'
    investment=state['investment']
    already=sum(original.startswith(prefix) for original in investment['originals'])
    if already>live['total_calls']:raise ValueError('Live original investment moved backwards')
    additional=live['total_calls']-already
    if additional==0:return state
    # A live job is unsealed, so it normally has no accepted-summary originals.
    # If a publication already covers a prefix, retain its exact cumulative
    # count and count only the newer portion of this same original namespace.
    investment['total_calls']+=additional
    current_counts={}
    for original,ref in investment['originals'].items():
        if original.startswith(prefix):current_counts[ref]=current_counts.get(ref,0)+1
    investment['live_calls']={ref:count-current_counts.get(ref,0) for ref,count in live['by_mechanism'].items()
        if count>current_counts.get(ref,0)}
    state=declare_investment(state,dict(mechanism_ref=live['direction'],predecessors=live.get('predecessors',[])))
    investment=state['investment']
    tail=min(additional,live['consecutive_calls'])
    related=_investment_ancestry(live['direction'],investment['lineage'])
    investment['consecutive_calls']=(investment['consecutive_calls']+tail
        if tail==additional and investment['mechanism_ref'] in related else tail)
    investment['mechanism_ref']=live['direction']
    return observe(state)


def request(value, *, budget_fraction=None, policy=None, automatic=False, final=False):
    state = observe(value, budget_fraction, selected_policy=policy)
    status = due(state, budget_fraction)
    return dict(**(dict(investment_cutoff=state['investment']['total_calls'])
        if state['policy']['version']==2 else {}),
        version=1, cutoff=status['cutoff'], due_reasons=status['reasons'],
        budget_fraction=status['budget_fraction'], automatic=bool(automatic), final=bool(final))


def worker_review(job, intent, *, summary, budget_fraction, selected_policy):
    """Project an actionable direction checkpoint from one accepted/current cut.

    A running W cannot publish accepted scientific rounds. Budget checkpoints
    therefore also bound the first long W without guessing progress from calls,
    scores, elapsed node targets or native context events. Only the ordinary
    post-return controller can freeze the returned W's scientific review input.
    """
    from . import delivery_review
    if (selected_policy is None or summary is None or job.get('role') != 'W'
            or delivery_review.scope(job) is not None
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
    if state['policy']['version']==2:
        investment=state['investment']
        # The immutable request carries its exact call cut, separate from rounds.
        boundary=frozen.get('investment_cutoff',investment['covered_calls'])
        if type(boundary) is not int or not investment['covered_calls']<=boundary<=investment['total_calls']:
            raise ValueError('Investment supervision cutoff moved outside accepted originals')
        investment['covered_calls']=boundary
        investment['consecutive_calls']=min(investment['consecutive_calls'],investment['total_calls']-boundary)
        if investment['count_due_call'] is not None and investment['count_due_call']<=boundary:
            investment['count_due_call']=None
            state['pending_due']=[reason for reason in state['pending_due'] if reason!='consecutive_calls']
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
    return dict(**(dict(investment={key:item for key,item in state['investment'].items() if key not in {'originals','lineage','live_calls'}},
        mechanism_investments=investment_counts(state),
        mode=state['policy']['mode']) if state['policy']['version']==2 else {}),
        version=1, direction=state['direction'], consecutive_rounds=state['consecutive_rounds'],
        total_rounds=state['total_rounds'], covered_rounds=state['covered_rounds'],
        cutoff=cutoff(state), last_covered_cutoff=copy.deepcopy(state['last_covered_cutoff']),
        due_reasons=status['reasons'], automatic_reviews=state['automatic_reviews'],
        recent_rounds=copy.deepcopy(state['recent_rounds']),
        rounds_omitted_count=max(0, state['total_rounds'] - len(state['recent_rounds'])),
        diagnostics=copy.deepcopy(state['diagnostics'][:4]),
        diagnostics_omitted_count=state.get('diagnostics_omitted_count',0)+max(0, len(state['diagnostics']) - 4),
        scope='Explicit W scientific experiment batches with new exact managed originals; counts grant no scientific qualification.')
