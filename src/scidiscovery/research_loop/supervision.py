"""Pure host policy and projections for explicit scientific exploration rounds.

The accepted-search publisher owns this small cumulative value. Original round
facts remain in its existing immutable index; neither agents nor this module
write a second ledger or query an execution backend.
"""
from __future__ import annotations

import copy
import math

ADAPTIVE_REFLECTION_POLICY = dict(version=4, mode='review', consecutive_calls=4,
    min_budget_fraction=0.10, budget_checkpoints=[], max_automatic_reviews=None,
    automatic_budget_fraction=dict(tokens=0.25, seconds=0.25))
DEFAULT_WORKER_FIRST_REFLECTION_POLICY = dict(ADAPTIVE_REFLECTION_POLICY,
    require_fresh_input=True)
DEFAULT_POLICY = DEFAULT_WORKER_FIRST_REFLECTION_POLICY
_UNOBSERVED = object()


def policy(value=None):
    value = copy.deepcopy(DEFAULT_POLICY if value is None else value)
    if (not isinstance(value, dict) or type(value.get('version')) is not int
            or value['version'] != 4):
        raise ValueError('Current scientific supervision policy version 4 required')
    value.setdefault('max_automatic_reviews',None)
    value.setdefault('require_fresh_input',True)
    if set(value) != set(DEFAULT_POLICY):
        raise ValueError('Exact scientific supervision policy required')
    if type(value['require_fresh_input']) is not bool:
        raise ValueError('Adaptive reflection fresh-input requirement must be boolean')
    if value['mode'] not in {'off','notice','review'}:
        raise ValueError('Explicit investment supervision mode required')
    fractions=value['automatic_budget_fraction']
    if (not isinstance(fractions,dict) or set(fractions)!={'tokens','seconds'}
            or any(_fraction(v) is None or not 0<v<=1 for v in fractions.values())
            or _fraction(value['min_budget_fraction']) is None
            or not 0<value['min_budget_fraction']<=1 or value['budget_checkpoints']!=[]):
        raise ValueError('Positive finite adaptive reflection budget fractions required')
    for field in ('consecutive_calls', 'max_automatic_reviews'):
        if field=='max_automatic_reviews' and value[field] is None:continue
        if type(value[field]) is not int or value[field] < (1 if field == 'consecutive_calls' else 0):
            raise ValueError('Finite scientific supervision counts required')
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
    return dict(version=1, policy=selected, direction=None, consecutive_rounds=0,
        total_rounds=0, covered_rounds=0, last_round=None, last_covered_cutoff=None,
        automatic_reviews=0, last_review_fraction=None, max_budget_fraction=None,
        covered_checkpoints=[], pending_due=[], count_due_round=None, recent_rounds=[], diagnostics=[],
        investment=dict(originals={},total_calls=0,consecutive_calls=0,
            mechanism_ref=None,covered_calls=0,count_due_call=None,lineage={}),
        adaptive=dict(question_revision=0,receipts=0,covered_question_revision=0,
            covered_receipts=0,last_completion=None,automatic_cost=dict(tokens=0,seconds=0.),
            cost_known=dict(tokens=True,seconds=True)))


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
    result['policy'] = frozen
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
    if chosen['mode']=='off':return state
    investment=state['investment']
    if investment['consecutive_calls']>=chosen['consecutive_calls'] and 'consecutive_calls' not in state['pending_due']:
        state['pending_due'].append('consecutive_calls')
        investment['count_due_call']=investment['total_calls']
    return state


def due(value, budget_fraction=None, policy=None, *, budget_facts=None):
    state = observe(value, budget_fraction, selected_policy=policy)
    return _adaptive_due(state,budget_facts,budget_fraction)


def add_investment(value,fact):
    """Count exact original calls once, without inventing scientific rounds."""
    state=_state(value)
    state=declare_investment(state,fact)
    original=fact['original_call_id'];ref=fact['mechanism_ref']
    if not isinstance(original,str) or not original or not isinstance(ref,str) or not ref:
        raise ValueError('Exact original call and declared mechanism required')
    investment=state['investment'];known=investment['originals'].get(original)
    if known is not None:
        if known!=ref:raise ValueError('Original investment changed its declared identity')
        return state
    investment['originals'][original]=ref
    if fact.get('completed_receipt') is True:
        state['adaptive']['receipts']+=1
    investment['total_calls']+=1
    related=_investment_ancestry(ref,investment['lineage'])
    investment['consecutive_calls']=(investment['consecutive_calls']+1
        if investment['mechanism_ref'] in related else 1)
    investment['mechanism_ref']=ref
    return observe(state)


def declare_investment(value,fact):
    """Retain declared ancestry for original-call unions, including no-call merges."""
    state=_state(value)
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
    if live is None:return state
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


def request(value, *, budget_fraction=None, policy=None, automatic=False, final=False, budget_facts=None):
    state = observe(value, budget_fraction, selected_policy=policy)
    status = due(state, budget_fraction,budget_facts=budget_facts)
    return dict(policy_version=4,source_w=value.get('last_w') if isinstance(value,dict) else None,
        budget_scope=copy.deepcopy(budget_facts),
        adaptive_cutoff=dict(question_revision=state['adaptive']['question_revision'],
            receipts=state['adaptive']['receipts']),
        investment_cutoff=state['investment']['total_calls'],
        review_kind='final_assessment' if final else 'ordinary_reflection',
        reflection_scope='worker_question_clues_evidence',
        version=1, cutoff=status['cutoff'], due_reasons=status['reasons'],
        budget_fraction=status['budget_fraction'], automatic=bool(automatic), final=bool(final))


def worker_review(job, intent, *, summary, budget_fraction, selected_policy, budget_facts=None, current_work=None):
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
    if job.get('advisory_scope_version')==1 and job.get('worker_continuation') is not None:
        # H/E advice did not assess the pending originals. Keep that frontier
        # uncovered while giving the exact resumed W an ordinary work interval.
        # These are current-task lower bounds, already owned by the publisher;
        # no new balance or inferred scientific progress is introduced.
        work=current_work or {}
        calls=work.get('calls')
        count_due=type(calls) is int and calls>=selected_policy.get('consecutive_calls',float('inf'))
        elapsed_due=selected_policy['min_budget_fraction']>0 and any(_fraction(work.get(name)) is not None and _fraction(limit) is not None
            and limit>0 and work[name]/limit>=selected_policy['min_budget_fraction']-1e-12
            for name,limit in (budget_facts or {}).get('limits',{}).items())
        if not count_due and not elapsed_due:
            return None
    status = due(summary, budget_fraction=budget_fraction, policy=selected_policy,budget_facts=budget_facts)
    if not status['automatic_allowed']:
        return None
    return dict(review_kind='ordinary_reflection',
        reasons=status['reasons'], cutoff=status['cutoff'],
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


def cover(value, frozen, *, source_step, completion_fraction=_UNOBSERVED, budget_facts=None):
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
    adaptive=state['adaptive'];cut=frozen['adaptive_cutoff']
    for field in ('question_revision','receipts'):
        if not adaptive['covered_'+field]<=cut[field]<=adaptive[field]:
            raise ValueError('Adaptive reflection cutoff moved outside accepted originals')
        adaptive['covered_'+field]=cut[field]
    adaptive['last_completion']=copy.deepcopy(budget_facts)
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


def project(value, *, budget_fraction=None, budget_facts=None):
    """Read-only scientific fields; private paths, bindings and tokens stay host-side."""
    state = observe(value, budget_fraction)
    status = due(state, budget_fraction,budget_facts=budget_facts)
    return dict(adaptive_review={key:copy.deepcopy(status[key]) for key in
            ('automatic_allowed','suppression_reason','fresh_input','operational_due')},
        automatic_cost=copy.deepcopy(state['adaptive']['automatic_cost']),
        automatic_cost_known=copy.deepcopy(state['adaptive']['cost_known']),
        investment={key:item for key,item in state['investment'].items() if key not in {'originals','lineage','live_calls'}},
        mechanism_investments=investment_counts(state), mode=state['policy']['mode'],
        version=1, direction=state['direction'], consecutive_rounds=state['consecutive_rounds'],
        total_rounds=state['total_rounds'], covered_rounds=state['covered_rounds'],
        cutoff=cutoff(state), last_covered_cutoff=copy.deepcopy(state['last_covered_cutoff']),
        due_reasons=status['reasons'], automatic_reviews=state['automatic_reviews'],
        recent_rounds=copy.deepcopy(state['recent_rounds']),
        rounds_omitted_count=max(0, state['total_rounds'] - len(state['recent_rounds'])),
        diagnostics=copy.deepcopy(state['diagnostics'][:4]),
        diagnostics_omitted_count=state.get('diagnostics_omitted_count',0)+max(0, len(state['diagnostics']) - 4),
        review_kind='ordinary_reflection',
        trigger_scope='consumption_and_original_call_investment',
        automatic_allowed=status['automatic_allowed'],cooldown=status['cooldown'],
        max_automatic_reviews=state['policy']['max_automatic_reviews'],
        min_budget_fraction=state['policy']['min_budget_fraction'],
        budget_fraction=status['budget_fraction'],
        scope='W-authored question, clues and evidence; consumption and original-call investment trigger reflection opportunities, never scientific progress verdicts.')


def authored_question_revision(value, changed):
    """Consume the publisher's exact full owner-fact comparison, never a digest."""
    state=_state(value)
    if changed is True:
        state['adaptive']['question_revision']+=1
    return state


def charge_automatic(value, request, *, cost, budget_facts=None, job=None, job_ref=None):
    """Retain every automatic ordinary A's actual original job cost, verdict-independent.

    Called once by the accepted-search publication transaction. Unknown coverage
    never clears known lower bounds and blocks subsequent automatic admission.
    """
    state=_state(value)
    if not isinstance(request,dict) or not request.get('automatic') or request.get('final'):
        return state
    if (not isinstance(job,dict) or job.get('role')!='A' or job.get('supervision_request')!=request
            or request.get('policy_version')!=4 or not isinstance(cost,dict)
            or cost.get('role')!='A' or cost.get('step')!=job.get('step')
            or job_ref is None or cost.get('job_ref')!=job_ref or cost.get('task_ref')!=job_ref):
        raise ValueError('Automatic reflection cost lost its exact accepted A job')
    validate_adaptive_request(state,request,job=job,budget_facts=budget_facts,source_w=request.get('source_w'))
    adaptive=state['adaptive']
    state['automatic_reviews']+=1
    # Admission coverage records inputs already presented, not an A verdict.
    # An invalid assessment must still cool down and cannot repeat its input.
    cut=request['adaptive_cutoff']
    for field in ('question_revision','receipts'):
        if not adaptive['covered_'+field]<=cut[field]<=adaptive[field]:
            raise ValueError('Adaptive reflection cutoff moved outside accepted originals')
        adaptive['covered_'+field]=cut[field]
    adaptive['last_completion']=copy.deepcopy(budget_facts)
    for dimension in ('tokens','seconds'):
        used=(cost or {}).get(dimension)
        if _fraction(used) is not None:adaptive['automatic_cost'][dimension]+=used
        adaptive['cost_known'][dimension] &= (_fraction(used) is not None
            and (cost or {}).get('complete',{}).get(dimension) is True)
    return state


def _adaptive_due(state, facts, fraction):
    """Admission only: counts/identity changes are opportunities, never verdicts."""
    chosen=state['policy'];adaptive=state['adaptive'];facts=facts or {}
    dimensions=[name for name in ('tokens','seconds') if facts.get('limits',{}).get(name) is not None]
    known=bool(dimensions) and all(_fraction(facts.get(name)) is not None
        and _fraction(facts['limits'][name]) is not None and facts['limits'][name]>0
        and facts.get('complete',{}).get(name) is True for name in dimensions)
    previous=adaptive['last_completion']
    baseline=previous or dict(tokens=0,seconds=0.)
    baseline_known=(state['last_covered_cutoff'] is None and state['automatic_reviews']==0 if previous is None else
        all(previous.get('complete',{}).get(name) is True
            and _fraction(previous.get(name)) is not None for name in dimensions))
    deltas={name:(facts[name]-baseline[name])/facts['limits'][name]
        for name in dimensions} if known and baseline_known else {}
    spacing=chosen['min_budget_fraction']
    cooldown=not deltas or max(deltas.values())<spacing-1e-12
    fresh=(adaptive['question_revision']>adaptive['covered_question_revision']
        or adaptive['receipts']>adaptive['covered_receipts'])
    operational=bool(deltas) and max(deltas.values())>=chosen['min_budget_fraction']-1e-12
    calls=state['investment']['total_calls']-state['investment']['covered_calls']
    reasons=[]
    if fresh and (calls>=chosen['consecutive_calls'] or deltas and max(deltas.values())>=spacing-1e-12):
        reasons.append('changed_authored_question_or_original_receipt')
    if operational and not fresh:reasons.append('operational_no_new_experiment')
    suppression=('mode_not_review' if chosen['mode']!='review' else
        'automatic_review_cost_unknown_reconcile_original_usage' if not all(adaptive['cost_known'][name] for name in dimensions) else
        'shared_consumption_unknown_reconcile_original_usage' if not known or not baseline_known else
        'automatic_review_budget_exhausted' if any(adaptive['automatic_cost'][name]>=
            chosen['automatic_budget_fraction'][name]*facts['limits'][name] for name in dimensions) else
        'emergency_review_count_cap' if chosen['max_automatic_reviews'] is not None
            and state['automatic_reviews']>=chosen['max_automatic_reviews'] else
        'no_new_review_input' if chosen.get('require_fresh_input',False) and not fresh else
        'cooldown' if cooldown else 'no_new_review_input' if not reasons else None)
    return dict(due=bool(reasons),reasons=reasons,cutoff=cutoff(state),
        automatic_allowed=bool(reasons) and suppression is None,cooldown=cooldown,
        automatic_reviews=state['automatic_reviews'],budget_fraction=state['max_budget_fraction'],
        current_budget_fraction=_fraction(fraction),suppression_reason=suppression,
        fresh_input=fresh,operational_due=operational)


def validate_adaptive_request(value, request, *, job, budget_facts, source_w, admission=False):
    """Validate frozen policy, exact accepted input frontier and raw budget scope."""
    from . import contracts
    state=_state(value)
    if request is None:return
    contracts.supervision_binding(request,step=job['step'],research_strategy_version=3,supervision_policy_version=4)
    if (request['source_w']!=source_w or request['source_w']>=job['step']
            or request['cutoff']!=cutoff(state)
            or not state['investment']['covered_calls']<=request['investment_cutoff']<=state['investment']['total_calls']):
        raise ValueError('Adaptive request changed its accepted W input scope')
    cut=request['adaptive_cutoff'];adaptive=state['adaptive']
    if any(not adaptive['covered_'+field]<=cut[field]<=adaptive[field] for field in ('question_revision','receipts')):
        raise ValueError('Adaptive request changed its accepted input frontier')
    frozen=request['budget_scope']
    if (not isinstance(budget_facts,dict) or frozen['limits']!=budget_facts.get('limits')
            or any(frozen[name] is not None and budget_facts.get(name) is not None
                and frozen[name]>budget_facts[name] for name in ('tokens','seconds'))):
        raise ValueError('Adaptive request changed its original shared budget scope')
    if admission and any(frozen['complete'][name] is True
            and budget_facts.get('complete',{}).get(name) is not True for name in ('tokens','seconds')):
        raise ValueError('Adaptive request overstated original budget coverage')
