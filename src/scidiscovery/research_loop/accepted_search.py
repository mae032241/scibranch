"""Small cumulative breadth-table snapshots and accepted scientific facts.

Each node copies and updates the complete mechanism table. Its cost is O(M)
in registered mechanisms, independently of sealed history length. Historical
node/call/candidate facts retain their existing exact-original addresses.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from urllib.parse import quote


VERSION = 2


def key(kind, identity):
    if kind not in {'mechanism', 'ordinal', 'work', 'candidate', 'node', 'call', 'cycle', 'due', 'judgment', 'repair', 'round', 'round_call'}:
        raise ValueError('Unknown accepted scientific fact kind')
    if not isinstance(identity, (str, int)) or isinstance(identity, bool):
        raise ValueError('Exact accepted scientific identity required')
    encoded=quote(str(identity),safe='')
    return 'search/' + kind + '/' + '/'.join(encoded[index:index+96] for index in range(0,len(encoded),96))


def empty():
    from . import supervision
    return dict(version=VERSION, node_count=0, mechanism_count=0, pending_count=0,
        remaining_count=0, owed_count=0, candidate_count=0, relation_count=0,
        failed_candidate_count=0, unknown_candidate_count=0, observation_count=0,
        partial_observation_count=0,
        coverage='unknown', current_candidate=None, a_recommended=None,
        historical_a_recommendation=None, actual_submission=None, latest_a_step=None,
        current_h=None,last_h=None,last_w=None,last_science=None,focus=None, allocation=None,
        table=dict(mechanisms={},ordinals=[],remaining=[],nominations=dict(by_mechanism_ref={},latest_extras=[],source_step=None,origin_identity=None)),current_facts={},extra_grant=None,
        opportunity_unknown_count=0, work_count=0, diagnostics=[],
        pending_work_response=None, supervision=supervision.empty(),
        portfolio=dict(completed_cycle_count=0, pending_w=None, last_branch=None,
            last_branches=[], last_decision_step=None, main_line=None,
            mainline_streak=0, mainline_source=None, overdue_count=0,
            unexcluded_count=0, latest_judgment_root=None,
            token_advice=dict(complete_sample_count=0,excluded_sample_count=0,reported_tokens_sum=0,samples=[])))


def _summary(value):
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != VERSION or not set(empty())<=value.keys():
        raise ValueError('Current accepted scientific summary required')
    return copy.deepcopy(value)


class _Delta:
    def __init__(self, summary, lookup):
        self.summary = _summary(summary)
        self.lookup = lookup
        self.rows = {}

    def get(self, kind, identity):
        if kind=='mechanism':return self.summary['table']['mechanisms'].get(identity)
        if kind=='ordinal':
            slots=self.summary['table']['ordinals']
            if type(identity) is not int or not 1<=identity<=len(slots):return None
            ref=slots[identity-1];item=self.summary['table']['mechanisms'][ref]
            return dict(ordinal=identity,mechanism_ref=ref,origin_identity=item['origin_identity'])
        name = key(kind, identity)
        if name not in self.rows:
            value = self.summary['current_facts'].get(name)
            if value is None:value=self.lookup(name)
            if value is not None:
                self.rows[name] = copy.deepcopy(value)
        return self.rows.get(name)

    def put(self, kind, identity, value):
        if kind=='mechanism':
            self.summary['table']['mechanisms'][identity]=value
            return value
        if kind=='ordinal':
            slots=self.summary['table']['ordinals']
            if identity!=len(slots)+1:raise ValueError('Mechanism ordinal must append to its local table')
            slots.append(value['mechanism_ref'])
            return value
        self.rows[key(kind, identity)] = value
        return value

    def queue(self, ref, item, planned_step=None):
        summary = self.summary
        remaining=summary['table']['remaining']
        current=next((row for row in remaining if row['mechanism_ref']==ref),None)
        if planned_step is not None:item['planned_step']=planned_step
        if current is None:remaining.append(dict(mechanism_ref=ref,planned_step=item['planned_step']))
        elif planned_step is not None:current['planned_step']=planned_step
        summary['remaining_count']=len(remaining)

    def unqueue(self, ref, item):
        summary = self.summary
        remaining=summary['table']['remaining']
        remaining[:]=[row for row in remaining if row['mechanism_ref']!=ref]
        summary['remaining_count']=len(remaining)


def _owed(item, initial_calls):
    # A may screen several authored observations from one comparative call.
    # That disposition releases the opportunity without inventing more calls;
    # reopening restores the ordinary check against the unchanged call count.
    return item['status'] == 'pending' and item.get('accepted_calls', 0) < initial_calls


def _status(delta, ref, item, status, initial_calls):
    summary = delta.summary
    summary['pending_count'] += int(status == 'pending') - int(item['status'] == 'pending')
    summary['portfolio']['unexcluded_count'] += int(status != 'waived') - int(item['status'] != 'waived')
    old_owed = _owed(item, initial_calls)
    item['status'] = status
    summary['owed_count'] += int(_owed(item, initial_calls)) - int(old_owed)
    if status == 'waived':
        delta.unqueue(ref, item)


def _due(delta, item, *, active=True, reset=False):
    portfolio=delta.summary['portfolio']
    threshold=portfolio.get('review_after_cycles')
    cycle=portfolio['completed_cycle_count']
    if reset:item['last_participated_cycle']=cycle
    if threshold is None:
        item['due_cycle']=None
        return
    item['due_cycle']=item.get('last_participated_cycle',cycle)+threshold if active else None


def _candidate(delta, item, step):
    from . import selection
    selection._id(item['id'])
    selection._public_keys(item)
    fact = {field: item.get(field) for field in selection.CANDIDATE_FIELDS}
    previous = delta.get('candidate', item['id'])
    if previous is not None:
        if previous['observation'] != fact:
            raise ValueError('Observed candidate identity changed')
        return previous
    delta.summary['candidate_count'] += 1
    delta.summary['failed_candidate_count'] += int(fact['status'] == 'failed')
    delta.summary['unknown_candidate_count'] += int(fact['status'] == 'unknown')
    return delta.put('candidate', item['id'], dict(observation=fact, first_step=step))


def _origin(root,step):
    from . import originals
    return copy.deepcopy(originals.node(root,step).origin_identity)


def _seal_ref(root,step):
    return dict(path=str(Path(root)/'native-evidence'/f'{step:03d}'/'result-identity.json'))


def _record_terminal(delta,fact):
    binding=fact['mechanism_binding']
    if binding is None or fact['status'] not in {'known_success','known_failed'}:return
    item=delta.get('mechanism',binding['mechanism_ref'])
    if item is None:raise ValueError('Terminal original has no registered mechanism')
    pointer={field:copy.deepcopy(fact[field]) for field in ('source_step','origin_identity','observation_ref',
        'call_index','call_id','mechanism_binding','recovery_ref')}
    previous=item.get('last_terminal')
    if previous is None or (pointer['source_step'],pointer['call_index'])>=(previous['source_step'],previous['call_index']):
        item['last_terminal']=pointer


def _screen_observations(job, payload, observation, delivery_facts):
    """Retain eligible exact observations, never a scientific screened verdict.

    The publication owner supplies already qualified delivery and call values.
    Bind them to this W's frozen mechanism versions before later W declarations
    can revise the current table. Reusing an observation creates no new call.
    """
    from . import worker_delivery
    if job['role']!='W':return []
    rows=[]
    if worker_delivery.applicable(job):
        if delivery_facts is None:
            raise ValueError('Accepted screen observations require produced delivery facts')
        bindings={row['mechanism_ref']:{key:copy.deepcopy(row[key])
            for key in ('mechanism_ref','source_step','source_ref','source_role')}
            for row in job['search']['breadth']['mechanisms']}
        evidence_rows=delivery_facts['evidence']
        # Resolve the complete original set once: two distinct originals with
        # the same call_id cannot become an unambiguous authored reference by
        # considering their rows separately.
        eligible=worker_delivery.finding_coverage(payload,evidence_rows,bindings)['observed']
        calls={}
        for evidence in evidence_rows:
            parts=Path(evidence['accepted']['path']).parts
            if len(parts)>=3 and parts[-3]=='calls' and parts[-1]=='accepted.json':
                calls.setdefault(parts[-2],set()).add(worker_delivery._evidence_key(evidence))
        referenced={}
        for finding in payload.get('mechanism_findings',[]) if isinstance(payload.get('mechanism_findings'),list) else []:
            if not isinstance(finding,dict) or finding.get('basis')!='observed':continue
            ref=finding.get('mechanism');call=finding.get('call_id')
            if isinstance(ref,str) and ref in eligible and isinstance(call,str) and len(calls.get(call,()))==1:
                referenced.setdefault(next(iter(calls[call])),set()).add(ref)
        for evidence in evidence_rows:
            refs={evidence['mechanism_ref']}|referenced.get(worker_delivery._evidence_key(evidence),set())
            for ref in bindings:
                if ref not in refs or ref not in eligible:continue
                rows.append(dict(mechanism_binding=bindings[ref],accepted=copy.deepcopy(evidence['accepted'])))
    else:
        for call in (observation or {}).get('managed_calls',[]):
            binding=call.get('mechanism_binding')
            if binding is not None and call['status'] in {'known_success','known_failed'}:
                rows.append(dict(mechanism_binding={key:copy.deepcopy(binding[key])
                    for key in ('mechanism_ref','source_step','source_ref','source_role')},accepted=copy.deepcopy(call['accepted'])))
    unique={}
    for row in rows:
        identity=(row['mechanism_binding']['mechanism_ref'],worker_delivery._evidence_key(row))
        prior=unique.setdefault(identity,row)
        if prior!=row:raise ValueError('Accepted screen observation identity changed')
    return list(unique.values())


def _live_mechanisms(delta, job, observation, *, origin, initial_calls):
    """Consume accepted call snapshots without reopening live declaration files.

    Call publication already bound the immutable declaration. The existing
    mechanism table gains stable identities; no second scientific ledger exists.
    """
    if job.get('research_strategy_version')!=3 or job['role']!='W':return
    snapshots=[(call.get('mechanism_binding') or {}).get('research_binding')
        for call in (observation or {}).get('managed_calls',[])]
    research=(observation or {}).get('research_state') or {}
    snapshots.extend(dict(version=1,**row) for row in research.get('mechanisms',{}).values())
    for row in snapshots:
        if row is None:continue
        if (not isinstance(row,dict) or row.get('version')!=1
                or not isinstance(row.get('mechanism_ref'),str) or not isinstance(row.get('source_ref'),dict)
                or not isinstance(row.get('question'),str) or not row['question'].strip()
                or type(row.get('revision')) is not int or row['revision']<0):
            raise ValueError('Accepted live mechanism lost its exact declaration')
        ref=row['mechanism_ref'];item=delta.get('mechanism',ref);summary=delta.summary
        if item is None:
            item=delta.put('mechanism',ref,dict(status='pending',original_step=row['source_step'],original_role=row['source_role'],
                accepted_calls=0,participated_cycles=0,last_participated_cycle=summary['portfolio']['completed_cycle_count']))
            summary['mechanism_count']+=1
            item.update(ordinal=summary['mechanism_count'],origin_identity=copy.deepcopy(origin),
                origin_ref=copy.deepcopy(row.get('origin_ref')) or dict(goal_id=origin['goal_id'],source_step=row['source_step'],source_role=row['source_role'],mechanism_ref=ref))
            delta.put('ordinal',item['ordinal'],dict(ordinal=item['ordinal'],mechanism_ref=ref,origin_identity=copy.deepcopy(origin)))
            summary['pending_count']+=1;summary['portfolio']['unexcluded_count']+=1
            summary['owed_count']+=int(_owed(item,initial_calls));_due(delta,item)
        # Revisions are local to an exact W owner. A new W's revision zero may
        # seed an old identity, but must never erase its accepted provenance.
        same_owner=item.get('source_step')==row['source_step']
        if same_owner and item.get('research_revision',-1)>row['revision']:continue
        if not same_owner and item.get('source_step',-1)>row['source_step']:continue
        if row.get('status')=='retired':_status(delta,ref,item,'waived',initial_calls)
        elif row['source_step']==job['step'] and item['status']=='waived':_status(delta,ref,item,'pending',initial_calls)
        item.update(label=row.get('label',row['question']),minimal_test=row['question'],
            source_step=row['source_step'],source_role=row['source_role'],source_ref=copy.deepcopy(row['source_ref']),
            source_origin_identity=copy.deepcopy(origin if row['source_step']==job['step'] else item.get('source_origin_identity',origin)),research_revision=row['revision'],
            research_original=copy.deepcopy(row.get('original_ref')),exploration=row.get('exploration',False),
            predecessors=copy.deepcopy(row.get('predecessors',[])),research_binding=copy.deepcopy(row))
        if item['status']!='waived':delta.queue(ref,item,row['source_step'])


def prepare_node(root, job, payload, seal, *, summary, lookup, observation, delivery_facts=None, usage_lookup=None,
        budget_fraction=None, completion_fraction=None):
    """Independent entry: produce this node's policy and exact original owner."""
    from . import contracts as c
    return prepare_node_from_facts(root,job,payload,seal,summary=summary,lookup=lookup,
        observation=observation,delivery_facts=delivery_facts,usage_lookup=usage_lookup,policy=c.settings(root,job),
        origin=_origin(root,job['step']),budget_fraction=budget_fraction,
        completion_fraction=completion_fraction)


def prepare_node_from_facts(root, job, payload, seal, *, summary, lookup, observation,
        policy, origin, delivery_facts=None, usage_lookup=None, budget_fraction=None, completion_fraction=None):
    """Copy the small breadth table and apply this accepted original's facts.

    ``lookup`` must read at the same canonical accepted head as ``summary``. The
    returned rows and summary are committed together by accepted_budget, which
    owns duplicate-event and interrupted-publication reconciliation.
    """
    from . import contracts as c, history, search
    if policy is None or origin is None:
        raise ValueError('Scientific publication requires its produced policy and origin')
    origin=copy.deepcopy(origin)
    delta = _Delta(summary, lookup)
    current = delta.summary
    round_bindings=copy.deepcopy(current['table']['mechanisms'])
    # Selected rows retain the task's exact source versions, even when this W
    # authors an existing_ref revision before its rounds are accepted. A screen
    # may additionally select the same frozen prefix's catalog members; those
    # already-produced pre-update table rows retain their exact source versions.
    frozen_breadth=(job.get('search') or {}).get('breadth') or {}
    round_bindings.update({row['mechanism_ref']:copy.deepcopy(row)
        for row in frozen_breadth.get('mechanisms',[])})
    step, role = job['step'], job['role']
    if delta.get('node', step) is not None:
        raise ValueError('Scientific original is already accepted at this head')
    limits = policy.get('search_policy') or {}
    threshold=(policy.get('portfolio_policy') or {}).get('review_after_cycles')
    prior_threshold=current['portfolio'].get('review_after_cycles')
    if current['node_count'] and prior_threshold!=threshold:
        raise ValueError('Accepted portfolio policy cannot change without an explicit rebuild')
    current['portfolio']['review_after_cycles']=threshold
    initial_calls = limits.get('initial_calls', 1)
    _live_mechanisms(delta,job,observation,origin=origin,initial_calls=initial_calls)
    control = seal.get('control_outcome', {})
    valid = all(control.get(field) == 'applied' for field in ('search_status', 'selection_status', 'decision_status'))
    ordinary = not (job.get('formal_review') or job.get('purpose') == 'formal_review' or job.get('engineering_repair'))
    current['node_count'] += 1
    if role=='H':current['last_h']=step
    if role=='W':current['last_w']=step
    current['relation_count'] += len(job.get('direct_refs', [])) + len(seal.get('selected_inputs', []))
    node = dict(step=step, role=role, next_action=payload.get('next_action'),
        stage_intent=payload.get('stage_intent'), purpose=job.get('purpose'),evidence_return=copy.deepcopy(job.get('evidence_return')),
        limitations=copy.deepcopy(payload.get('limitations',[])),delivery=copy.deepcopy(control.get('worker_delivery')),focus=copy.deepcopy((job.get('search') or {}).get('focus')),
        allocation=copy.deepcopy((job.get('search') or {}).get('allocation')),
        breadth=bool((job.get('search') or {}).get('breadth')), assessed=False,portfolio_assessed=False,
        direct_steps=[int(row['path'].split('/')[2]) for row in job.get('direct_refs',[])],
        pending_executions=copy.deepcopy(seal.get('pending_executions')),
        pending_calls=[], pending_namespace_unavailable=False,
        origin_identity=copy.deepcopy(origin),screen_observations=_screen_observations(job,payload,observation,delivery_facts))
    delta.put('node', step, node)
    request=current['pending_work_response']
    if request:
        author=delta.get('node',request['source_a'])
        expected={'continue_w':'W','revise_h':'H','extract':'E'}.get(author['next_action'])
        direct=any(row['path']==f'inputs/parents/{request["source_a"]:03d}/conclusion.json' for row in job.get('direct_refs',[]))
        completed=role==expected and direct and (role!='E' or job.get('evidence_return')==dict(step=request['source_a'],role='A'))
        if completed:
            for ref in author.get('executed_work',[]):
                item=delta.get('work',ref)
                if item is None:raise ValueError('Accepted scientific work request is absent')
                item['executions'][-1]['response']=dict(source_step=step,role=role,pointer='/scientific_result')
            current['pending_work_response']=None
    if role=='W' and node['focus'] and node['focus']['action']=='repair':
        repair_root=job['search']['repair_root']
        repair=delta.get('repair',repair_root)
        if repair is None:repair=delta.put('repair',repair_root,dict(accepted_count=0))
        repair['accepted_count']+=1
    consultation=job.get('purpose')=='worker_consultation'
    producer=search.mechanism_producer(job)
    if ordinary and producer and (role=='H' and not consultation or isinstance(payload.get('search_plan'),dict)):
        current['last_science'] = dict(step=step, role=role)
    if ordinary and valid and role!='A':
        current['focus'] = copy.deepcopy(control.get('resolved_focus'))
        current['allocation'] = copy.deepcopy(control.get('resolved_allocation'))
        current['diagnostics'] = copy.deepcopy(control.get('diagnostics', []))
        if producer:
            plan = payload.get('search_plan') or {}
            if role=='H' and (not consultation or plan.get('commitments')):current['current_h'] = step
            for index, authored in enumerate(plan.get('commitments', []), 1):
                ref = authored.get('existing_ref') or f'{role.lower()}{step:03d}.m{index}'
                item = delta.get('mechanism', ref)
                if item is None:
                    item = delta.put('mechanism', ref, dict(status='pending', original_step=step,original_role=role,
                        accepted_calls=0, participated_cycles=0, last_participated_cycle=current['portfolio']['completed_cycle_count']))
                    current['mechanism_count'] += 1
                    item.update(ordinal=current['mechanism_count'],origin_identity=origin,
                        origin_ref=dict(goal_id=origin['goal_id'],source_step=step,source_role=role,mechanism_ref=ref))
                    delta.put('ordinal',item['ordinal'],dict(ordinal=item['ordinal'],mechanism_ref=ref,origin_identity=origin))
                    current['pending_count'] += 1
                    current['portfolio']['unexcluded_count'] += 1
                    current['owed_count'] += 1
                    _due(delta,item)
                else:
                    was_waived=item['status']=='waived'
                    _status(delta, ref, item, 'pending', initial_calls)
                    if was_waived:_due(delta,item,reset=True)
                    item.pop('disposition', None)
                item.update(label=authored['label'], minimal_test=search.minimal_test_text(authored.get('minimal_test',authored['label'])), source_step=step,
                    source_role=role,source_origin_identity=origin,source_ref=_seal_ref(root,step))
                delta.queue(ref, item, step)
                if current['extra_grant'] and current['extra_grant']['mechanism_ref'] == ref:
                    current['extra_grant'] = None
    elif ordinary and role!='A':
        current['focus'] = None
        current['allocation'] = None
        current['diagnostics'] = copy.deepcopy(control.get('diagnostics', []))
    if node['pending_executions'] is not None:
        from . import originals
        source = originals.node(root,step)
        pending = c.read(originals.resolve_ref(source,node['pending_executions']))
        known = {row['call_id'] for row in (observation or {}).get('managed_calls', [])
            if row['status'] != 'unknown'}
        calls = [row['call_id'] for row in pending['allocations']
            if row['disposition'] == 'possibly_prepared']
        for ref in pending.get('host_preparations', []):
            owner = originals.source_for_ref(source,ref)
            calls.append(c.read(originals.resolve_ref(owner,ref))['call_id'])
        node['pending_calls'] = [dict(call_id=call,recovery_ref=None)
            for call in dict.fromkeys(call for call in calls if call not in known)]
        node['pending_namespace_unavailable'] = (pending.get('complete') is False
            and bool(pending.get('unavailable_reason')) and not pending['allocations']
            and not pending.get('host_preparations'))
    if observation is None:
        if current['coverage'] != 'partial':current['coverage'] = 'partial'
    else:
        coverage = observation['coverage']['status']
        current['observation_count'] += 1
        current['partial_observation_count'] += int(coverage != 'complete')
        current['coverage'] = 'partial' if current['coverage'] == 'partial' or coverage != 'complete' else 'complete'
        for fact in observation.get('candidates', []):_candidate(delta, fact, step)
        for index,context in enumerate(observation.get('contexts',[])):
            identity=context.get('candidate_id')
            if identity is None:continue
            item=delta.get('candidate',identity)
            if item is None:raise ValueError('Accepted context has no observed candidate')
            parent=(context.get('parent') or {}).get('candidate_id')
            item.update(source_step=step,call_id=context['call_id'],
                mechanism_ref=(context.get('mechanism_binding') or {}).get('mechanism_ref',(context.get('scope') or {}).get('mechanism_ref')),
                parent=parent,execution_status=context['status'],context_pointer=f'/contexts/{index}')
            current['relation_count']+=int(parent is not None)
        current['current_candidate'] = copy.deepcopy(observation.get('current_candidate'))
        if observation.get('actual_submission') is not None:
            current['actual_submission'] = dict(copy.deepcopy(observation['actual_submission']), observed_at=step)
        managed=observation.get('managed_calls',[])
        call_origin=origin if managed else None
        for call_index,call in enumerate(managed):
            identity = f'{step}:{call["call_id"]}'
            if delta.get('call', identity) is not None:
                raise ValueError('Managed scientific call accepted twice')
            fact=delta.put('call',identity,dict(step=step,source_step=step,call_id=call['call_id'],status=call['status'],
                mechanism_binding=copy.deepcopy(call.get('mechanism_binding')),origin_identity=copy.deepcopy(call_origin),
                observation_ref=copy.deepcopy(seal['observations']),call_index=call_index,recovery_ref=None))
            _record_terminal(delta,fact)
            binding = call.get('mechanism_binding')
            if binding is None:continue
            current['opportunity_unknown_count'] += int(call['status'] == 'unknown')
            item = delta.get('mechanism', binding['mechanism_ref'])
            if item is None:raise ValueError('Accepted call has no registered mechanism')
            owed = _owed(item, initial_calls)
            item['accepted_calls'] += 1
            current['owed_count'] += int(_owed(item, initial_calls)) - int(owed)
            grant = current['extra_grant']
            if binding.get('grant') is not None and grant and binding['grant']['source_a'] == grant['source_a']:
                grant['used_calls'] += 1
    if role == 'A' and not consultation and payload.get('next_action')!='audit':
        candidate = payload.get('candidate_recommendation')
        # Only an original visible before this A may occupy its authored slot.
        # An unresolved optional citation is retained in the raw original, not
        # promoted to a recommendation or a new scientific execution grant.
        if not isinstance(candidate,str) or not candidate or len(candidate.encode())>160 or lookup(key('candidate',candidate)) is None:candidate=None
        if current['a_recommended'] is not None:
            current['historical_a_recommendation'] = current['a_recommended']
        current['latest_a_step'] = step
        current['a_recommended'] = dict(candidate_id=candidate, author_step=step) if candidate is not None else None
    _portfolio(delta, job, payload, observation, valid and ordinary and not consultation, initial_calls,usage_lookup)
    _work(delta, job, payload, valid and ordinary and not consultation)
    _supervision(delta,job,payload,seal,observation,origin=origin,
        bindings=round_bindings,valid=valid and ordinary and not consultation,policy=policy,
        budget_fraction=budget_fraction,completion_fraction=completion_fraction)
    if role=='W' and job.get('research_strategy_version')==3:
        from . import supervision
        for row in ((observation or {}).get('research_state') or {}).get('mechanisms',{}).values():
            current['supervision']=supervision.declare_investment(current['supervision'],dict(
                mechanism_ref=row['mechanism_ref'],predecessors=copy.deepcopy(row.get('predecessors',[]))))
        for call in (observation or {}).get('managed_calls',[]):
            binding=call.get('mechanism_binding')
            if binding is None:continue
            call_identity=json.dumps(call['scope']['job_ref'],sort_keys=True,separators=(',',':'))+':'+call['call_id']
            current['supervision']=supervision.add_investment(current['supervision'],dict(
                original_call_id=call_identity,mechanism_ref=binding['mechanism_ref'],
                predecessors=copy.deepcopy((binding.get('research_binding') or {}).get('predecessors',[]))))
    if role=='W' and job.get('candidate_nomination_mechanisms'):
        from . import candidate_nominations
        authored=c.scientific_field(job,payload,'candidate_nominations')
        available=set()
        if isinstance(authored,dict):
            for kind in ('representatives','extras'):
                for row in authored.get(kind,[]) if isinstance(authored.get(kind),list) else []:
                    identity=row.get('candidate') if isinstance(row,dict) else None
                    if isinstance(identity,str) and len(identity.encode())<=160 and delta.get('candidate',identity) is not None:
                        available.add(identity)
        declaration=candidate_nominations._preview(authored,available=available)
        if declaration is not None:
            nominations=current['table']['nominations']
            nominations.update(source_step=step,origin_identity=origin,
                diagnostics=copy.deepcopy(declaration['diagnostics']),
                latest_extras=copy.deepcopy([row for row in declaration['extras'] if isinstance(row.get('candidate'),str)]))
            rows=[(kind,row) for kind in ('representatives','gaps') for row in declaration[kind]
                if kind=='gaps' or isinstance(row.get('candidate'),str)]
            for index,(kind,row) in enumerate(rows):
                previous=nominations['by_mechanism_ref'].get(row['mechanism_ref'])
                nominations['by_mechanism_ref'][row['mechanism_ref']]=dict(kind=kind,declaration=copy.deepcopy(row),
                    source_step=step,origin_identity=origin,order=previous['order'] if previous else [step,index])
    portfolio=current['portfolio'];threshold=portfolio.get('review_after_cycles')
    portfolio['overdue_count']=sum(item['status']!='waived' and item.get('due_cycle') is not None
        and item['due_cycle']<=portfolio['completed_cycle_count'] for item in current['table']['mechanisms'].values()) if threshold is not None else 0
    _retain_current(delta,step)
    return dict(summary=current, rows=delta.rows)


def _retain_current(delta,step=None):
    """Keep only current/unfinished dependencies alongside the breadth table."""
    summary=delta.summary
    available=dict(summary['current_facts']);available.update(delta.rows)
    selected={}
    def retain(kind,identity):
        if identity is None:return None
        name=key(kind,identity);value=available.get(name)
        if value is None:value=delta.lookup(name)
        if value is not None:selected[name]=copy.deepcopy(value)
        return value
    for name,value in available.items():
        if (name.startswith('search/work/') and value['status']!='withdrawn'
                or name.startswith('search/call/') and value['status']=='unknown'
                or name.startswith('search/node/')
                and (value['pending_calls'] or value['pending_namespace_unavailable'])):
            selected[name]=copy.deepcopy(value)
    if step is None:
        latest = max((value['step'] for name,value in available.items()
            if name.startswith('search/node/')), default=None)
        retain('node',latest)
    else:retain('node',step)
    if summary['last_science']:retain('node',summary['last_science']['step'])
    portfolio=summary['portfolio'];pending=portfolio['pending_w']
    if pending:
        for member in pending.get('steps',[pending['step']]):retain('node',member)
        retain('node',pending['cycle_root'])
        retain('cycle',pending['cycle_root']);retain('repair',pending['cycle_root'])
    request=summary['pending_work_response']
    if request:retain('node',request['source_a'])
    latest=portfolio['latest_judgment_root']
    if latest is not None:
        judgment=retain('judgment',latest)
        retain('node',latest)
        if judgment:retain('node',judgment['source_step'])
    for name,value in list(selected.items()):
        if name.startswith('search/node/') and value.get('assessed_cycle_root') is not None:
            retain('cycle',value['assessed_cycle_root'])
    summary['current_facts']=selected


def search_completed(job,payload):
    from . import search
    return search.completed_worker(job,payload)


def _portfolio(delta, job, payload, observation, valid, initial_calls,usage_lookup):
    summary, step = delta.summary, job['step']
    portfolio = summary['portfolio']
    if job['role'] == 'W':
        focus = (job.get('search') or {}).get('focus')
        if focus is not None:
            branches = [focus['mechanism_ref']]
            if (job.get('search') or {}).get('breadth'):
                branches = list(dict.fromkeys(row['mechanism_binding']['mechanism_ref'] for row in (observation or {}).get('managed_calls', []) if row.get('mechanism_binding')))
            old=portfolio['pending_w']
            direct=delta.get('node',step)['direct_steps']
            linked=bool(old and old['step'] in direct)
            if linked:
                branches=list(dict.fromkeys([*old.get('all_branches',old.get('branches',[old['branch']])),*branches]))
            delta.get('node',step)['branches']=branches
            portfolio['pending_w'] = dict(step=step, branch=focus['mechanism_ref'], branches=branches[:4],branch_count=len(branches),
                all_branches=branches,steps=[*old.get('steps',[old['step']]),step] if linked else [step],
                completed=search_completed(job,payload), repair=focus['action'] == 'repair' or bool(linked and old['repair']),
                cycle_root=old['cycle_root'] if linked else job['search']['repair_root'] if focus['action']=='repair' else step)
            if focus['action']=='repair':_exclude_cycle(delta,job['search']['repair_root'])
        return
    if job['role'] != 'A' or not valid:return
    if job.get('author_correction'):
        corrected=delta.get('node',job['author_correction']['root']['step'])
        if corrected and corrected.get('assessed_cycle_root') is not None:_exclude_cycle(delta,corrected['assessed_cycle_root'])
    pending = portfolio['pending_w'];focus = summary['focus']
    pending_node=delta.get('node',pending['step']) if pending else None
    branches=pending.get('all_branches',pending_node.get('branches',[pending['branch']])) if pending_node else []
    final = job.get('final_report') or job.get('final_only') or job.get('purpose') == 'closeout'
    direct = bool(pending and any(row['path'] == f'inputs/parents/{pending["step"]:03d}/conclusion.json' for row in job.get('direct_refs', [])))
    original_cycle=True
    if job.get('author_correction'):
        original=delta.get('node',job['author_correction']['root']['step'])
        original_cycle=bool(original and original['role']=='A' and pending and pending['step'] in original['direct_steps'])
    repairing = focus and focus['action'] == 'repair' and pending and focus['mechanism_ref'] in branches
    if original_cycle and pending and pending['completed'] and (direct or final) and payload.get('next_action') not in {'audit', 'formal_review', 'extract', 'wait'}:
        old = delta.get('node', pending['step'])
        if old is None:raise ValueError('Pending portfolio W original is absent')
        cycle_node=delta.get('node',pending['cycle_root'])
        if cycle_node is None:raise ValueError('Pending portfolio cycle original is absent')
        if not cycle_node['portfolio_assessed']:
            cycle_node['portfolio_assessed'] = True
            portfolio['completed_cycle_count'] += 1
            cycle = portfolio['completed_cycle_count']
            for ref in branches:
                item = delta.get('mechanism', ref)
                item['participated_cycles'] += 1
                _due(delta,item,active=item['status']!='waived',reset=True)
            portfolio['last_branch'] = pending['branch'] if len(branches) == 1 else None
            portfolio['last_branches'] = branches[:4]
            portfolio['last_branch_count'] = len(branches)
            sample_complete=False;reported=None
            if usage_lookup is not None:
                worker_usage=[usage_lookup(member) for member in pending.get('steps',[pending['step']])]
                a_usage=usage_lookup(step)
                sample_complete=bool(a_usage and a_usage['complete'] and all(row and row['complete'] for row in worker_usage) and not pending['repair'] and not job.get('author_correction'))
                if sample_complete:reported=sum(row['reported_tokens'] for row in worker_usage)+a_usage['reported_tokens']
            sample=dict(w_step=pending['step'],a_step=step,reported_tokens=reported)
            advice=portfolio['token_advice']
            advice['complete_sample_count']+=int(sample_complete)
            advice['excluded_sample_count']+=int(not sample_complete)
            if sample_complete:
                advice['reported_tokens_sum']+=reported
                advice['samples']=(advice['samples']+[sample])[-2:]
            delta.put('cycle', pending['cycle_root'], dict(w_step=pending['step'],w_steps=copy.deepcopy(pending.get('steps',[pending['step']])),a_step=step, mechanism_refs=branches,
                repair=pending['repair'],sample_complete=sample_complete,reported_tokens=reported))
            delta.get('node',step)['assessed_cycle_root']=pending['cycle_root']
        for member in pending.get('steps',[pending['step']]):
            old=delta.get('node',member)
            if old is None:raise ValueError('Pending portfolio chain original is absent')
            if old['assessed']:continue
            old['assessed']=True
        portfolio['pending_w'] = None
    if payload.get('next_action') in {'continue_w', 'revise_h', 'finish'} and (payload.get('scientific_result') or payload.get('route_reason')):
        portfolio['last_decision_step'] = step


def _exclude_cycle(delta,root_step):
    cycle=delta.get('cycle',root_step)
    if cycle is None or not cycle.get('sample_complete'):return
    advice=delta.summary['portfolio']['token_advice']
    advice['complete_sample_count']-=1;advice['excluded_sample_count']+=1
    advice['reported_tokens_sum']-=cycle['reported_tokens']
    advice['samples']=[sample for sample in advice['samples'] if sample['w_step']!=cycle['w_step']]
    cycle['sample_complete']=False


def _work(delta, job, payload, valid):
    # Optional portfolio advice never creates or executes scientific work.
    return


def _supervision(delta, job, payload, seal, observation, *, origin, bindings, valid, policy,
        budget_fraction=None, completion_fraction=None):
    """Accept explicit experiment batches against this W's produced originals.

    Only host-produced observation facts and the pre-update stable mechanism
    table are consumed. Failed optional declarations remain authored originals;
    they never trigger output repair or synthesize scientific rounds.
    """
    from . import contracts as c, supervision
    state=delta.summary['supervision']
    chosen=supervision.policy(policy.get('supervision_policy'))
    if delta.summary['node_count']==1 and not state['total_rounds']:
        state=supervision.empty(chosen)
    elif state['policy']!=chosen:
        raise ValueError('Accepted scientific supervision policy cannot change')
    request=job.get('supervision_request')
    fraction=request.get('budget_fraction') if isinstance(request,dict) else None
    state=supervision.observe(supervision.observe(state,fraction),budget_fraction)
    diagnostics=[]
    def diagnostic(index,reason):
        diagnostics.append(dict(source_step=job['step'],pointer=f'/exploration_rounds/{index}' if index is not None else '/exploration_rounds',reason=reason))
    if job['role']=='W' and valid and job.get('research_strategy_version')!=3:
        declarations=c.scientific_field(job,payload,'exploration_rounds')
        if declarations is None:
            diagnostic(None,'no_declared_scientific_rounds')
        elif not isinstance(declarations,list):
            diagnostic(None,'invalid_scientific_round_declaration')
        else:
            calls={}
            for call_index,call in enumerate((observation or {}).get('managed_calls',[])):
                calls.setdefault(call.get('call_id'),[]).append((call_index,call))
            for index,authored in enumerate(declarations):
                if (not isinstance(authored,dict) or set(authored)!={'mechanism','question','call_ids','finding'}
                        or any(not isinstance(authored.get(field),str) or not authored[field].strip() for field in ('mechanism','question','finding'))
                        or not isinstance(authored.get('call_ids'),list) or not authored['call_ids']
                        or any(not isinstance(call,str) or not call for call in authored['call_ids'])
                        or len(set(authored['call_ids']))!=len(authored['call_ids'])):
                    diagnostic(index,'invalid_scientific_round_declaration');continue
                ref=authored['mechanism'];expected=bindings.get(ref)
                if expected is None:
                    diagnostic(index,'unbound_direction');continue
                evidence=[];identities=[];reason=None
                for identity in authored['call_ids']:
                    matches=calls.get(identity,[])
                    if len(matches)!=1:
                        reason='ambiguous_or_missing_current_w_call';break
                    call_index,call=matches[0];binding=call.get('mechanism_binding')
                    proof=call.get('accepted')
                    if call.get('status') not in {'known_success','known_failed'}:
                        reason='original_outcome_unknown';break
                    if (not isinstance(binding,dict) or binding.get('mechanism_ref')!=ref
                            or any(binding.get(key)!=expected.get(key) for key in ('source_step','source_ref','source_role'))):
                        reason='exact_direction_binding_mismatch';break
                    if (not isinstance(proof,dict) or not isinstance(proof.get('path'),str)
                            or type(proof.get('size_bytes')) is not int or proof['size_bytes']<0):
                        reason='exact_call_original_unavailable';break
                    # The source owner and original logical path identify a
                    # call. Byte size and archive snapshot aliases do not.
                    proof_key=json.dumps(dict(origin_identity=origin,path=proof['path']),
                        sort_keys=True,separators=(',',':'))
                    if proof_key in identities or delta.get('round_call',proof_key) is not None:
                        reason='original_already_counted';break
                    identities.append(proof_key)
                    evidence.append(dict(call_id=identity,call_index=call_index,
                        status=call['status'],accepted=copy.deepcopy(proof),
                        mechanism_binding=copy.deepcopy(binding),observation_ref=copy.deepcopy(seal.get('observations'))))
                if reason:
                    diagnostic(index,reason);continue
                round_id=f'w{job["step"]:03d}.r{index+1}'
                fact=dict(round_id=round_id,source_step=job['step'],source_role='W',
                    origin_identity=copy.deepcopy(origin),pointer=f'/exploration_rounds/{index}',
                    scientific_original_ref=copy.deepcopy(seal.get('raw_ref')),
                    mechanism=ref,question=authored['question'],finding=authored['finding'],
                    call_ids=copy.deepcopy(authored['call_ids']),evidence=evidence,
                    ordinal=state['total_rounds']+1)
                delta.put('round',round_id,fact)
                for identity in identities:
                    delta.put('round_call',identity,dict(round_id=round_id,source_step=job['step']))
                state=supervision.add_round(state,fact)
    if diagnostics:
        state['diagnostics']=diagnostics[:4]
        state['diagnostics_omitted_count']=max(0,len(diagnostics)-4)
    result=c.scientific_field(job,payload,'supervision_result') if job['role']=='A' else None
    science=payload.get('scientific_result')
    meaningful=isinstance(science,str) and bool(science.strip())
    routes={'continue_existing':{'continue_w'},'request_evidence':{'continue_w','revise_h','extract'},'finalize':{'finish','deliver'}}
    if (job['role']=='A' and valid and isinstance(request,dict) and meaningful
            and isinstance(result,str) and payload.get('next_action') in routes.get(result,set())):
        frozen=request.get('cutoff') or {};count=frozen.get('total_rounds')
        original=delta.get('round',frozen['round_id']) if isinstance(frozen.get('round_id'),str) else None
        bound=(type(count) is int and state['covered_rounds']<=count<=state['total_rounds'] and
            (count==0 and frozen.get('source_step') is None and frozen.get('round_id') is None
             or original is not None and original['ordinal']==count and original['source_step']==frozen.get('source_step')
                and original['source_step']<job['step']))
        if bound:
            state=supervision.cover(state,request,source_step=job['step'],completion_fraction=completion_fraction)
    delta.summary['supervision']=state


def prepare_recovery(*, summary, lookup, step, call_id, resolved,recovery_ref):
    """Reconcile one already consumed original call; never create an opportunity.

    The caller owns original-call evidence qualification. A recovery can supply
    terminal knowledge for this exact identity, but cannot change its binding or
    erase its original contribution to accepted-call counters.
    """
    delta = _Delta(summary, lookup)
    identity = f'{step}:{call_id}'
    original = delta.get('call', identity)
    node = delta.get('node',step)
    if node is None:
        raise ValueError('Recovery requires its accepted original scientific node')
    pending = node['pending_calls']
    pointer = next((item for item in pending if item['call_id'] == call_id),None)
    if resolved is None or resolved['status'] == 'unknown':
        if pointer is None:
            pointer = dict(call_id=call_id,recovery_ref=None)
            pending.append(pointer)
        pointer['recovery_ref'] = copy.deepcopy(recovery_ref)
    elif pointer is not None:
        pending.remove(pointer)
    if original is None:
        # A sealed unprepared allocation has not consumed a scientific call.
        # Its resume intent still owns an unfinished original, even with no row.
        _retain_current(delta)
        return dict(summary=delta.summary, rows=delta.rows)
    if resolved is None:
        _retain_current(delta)
        return dict(summary=delta.summary, rows=delta.rows)
    if (resolved.get('call_id') != call_id
            or resolved.get('mechanism_binding') != original['mechanism_binding']
            or resolved.get('status') not in {'known_success', 'known_failed', 'unknown', 'not_submitted'}):
        raise ValueError('Recovery changed the original scientific call binding')
    if original['status'] != 'unknown' and resolved['status'] != original['status']:
        raise ValueError('Recovery cannot replace known original scientific facts')
    if original['mechanism_binding'] is not None:
        delta.summary['opportunity_unknown_count'] += int(resolved['status'] == 'unknown') - int(original['status'] == 'unknown')
    original['status'] = resolved['status']
    original['recovery_ref']=copy.deepcopy(recovery_ref)
    _record_terminal(delta,original)
    # Terminal knowledge is not a new observer qualification of model outputs.
    delta.summary['coverage']='partial'
    _retain_current(delta)
    return dict(summary=delta.summary, rows=delta.rows)


def requested_refs(job, payload=None):
    """Read only author-addressed entity identities, without a history scan."""
    payload = payload or {}
    def obj(value):return value if isinstance(value,dict) else {}
    def rows(value):return value if isinstance(value,list) else []
    mechanisms, works = set(), set()
    for row in rows(obj(payload.get('search_plan')).get('commitments')):
        if isinstance(row, dict) and isinstance(row.get('existing_ref'), str):mechanisms.add(row['existing_ref'])
    for field in ('final_coverage_mechanisms', 'candidate_nomination_mechanisms'):
        for row in job.get(field, []):
            if isinstance(row, dict) and isinstance(row.get('mechanism_ref'), str):mechanisms.add(row['mechanism_ref'])
    return mechanisms, works


def state(root, before_step, *, refs=(), work_refs=(), head=None):
    """Copy the local small breadth table; historical facts remain addressed."""
    from . import accepted_budget
    head = accepted_budget.read(root) if head is None else head
    summary = _summary(head['search'])
    if head['prefix']['cutoff'] >= before_step:
        raise ValueError('Accepted scientific head is outside this task prefix')
    mechanisms=summary['table']['mechanisms']
    works = []
    for ref in work_refs:
        name=key('work',ref)
        item=summary['current_facts'].get(name)
        if item is None:item=accepted_budget.search_row(root,name,head=head)
        if item is not None:works.append(item)
    remaining=copy.deepcopy(summary['table']['remaining'])
    portfolio = dict(summary['portfolio'], invested={ref:item.get('participated_cycles', 0) for ref,item in mechanisms.items()},
        deferred={ref:summary['portfolio']['completed_cycle_count']-item.get('last_participated_cycle',0) for ref,item in mechanisms.items()},
        seen_w=set(), cycles=[], mainline_reviews=[])
    def point_get(ref):
        return mechanisms.get(ref)
    def node_get(step):
        name=key('node',step)
        return summary['current_facts'].get(name) or accepted_budget.search_row(root,name,head=head)
    def repair_get(step):
        name=key('repair',step)
        row=summary['current_facts'].get(name) or accepted_budget.search_row(root,name,head=head)
        return row['accepted_count'] if row else 0
    return dict(summary=summary, _catalog=dict(prefix=copy.deepcopy(head['prefix']),max_ordinal=summary['mechanism_count']),
        worker_research_mechanisms=[copy.deepcopy(item['research_binding']) for item in mechanisms.values()
            if isinstance(item.get('research_binding'),dict)],
        _point_get=point_get, _node_get=node_get, _repair_get=repair_get, mechanisms=mechanisms, current_h=summary['current_h'], scope=None, plan=None,
        authorized=set(mechanisms), focus=summary['focus'], extra_grant=summary['extra_grant'],
        opportunity_counts={ref:item['accepted_calls'] for ref,item in mechanisms.items()}, opportunity_grants={},
        opportunity_unknown=bool(summary['opportunity_unknown_count']), accepted_slices=[], repairs={},
        order=dict(remaining=remaining, seen_w=set(), allocation=summary['allocation'], diagnostics=summary['diagnostics'], discriminating_work=works), portfolio=portfolio)


def catalog_item(root,catalog,choice,*,head=None):
    """Resolve one selected stable number/ref at this exact frozen prefix."""
    from . import accepted_budget
    head=accepted_budget.read(root) if head is None else head
    prefix=catalog['prefix']
    if head['prefix']!=prefix:
        raise ValueError('Scientific admission requires its exact frozen accepted prefix')
    if catalog['max_ordinal']!=head['search']['mechanism_count']:
        raise ValueError('Mechanism catalog count changed at its frozen prefix')
    if isinstance(choice,str) and choice.isdecimal():
        if choice!=str(int(choice)) or int(choice)<1:raise ValueError('Choose a positive catalog mechanism number')
        choice=int(choice)
    if type(choice) is int:
        if not 1<=choice<=catalog['max_ordinal']:raise ValueError('Mechanism number is outside this frozen catalog')
        slots=head['search']['table']['ordinals']
        if choice>len(slots):raise ValueError('Accepted mechanism catalog member is absent')
        ref=slots[choice-1]
    elif isinstance(choice,str):
        ref=choice
    else:raise ValueError('Choose one exact mechanism catalog number')
    item=head['search']['table']['mechanisms'].get(ref)
    if item is None or item['ordinal']>catalog['max_ordinal']:
        raise ValueError('Mechanism is outside this frozen catalog')
    if type(choice) is int and item['ordinal']!=choice:
        raise ValueError('Mechanism number changed its exact original identity')
    source=dict(origin_identity=item['source_origin_identity'],seal_ref=item['source_ref'])
    return ref,item,head,source
