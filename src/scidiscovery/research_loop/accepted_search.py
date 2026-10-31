"""Small cumulative breadth-table snapshots and accepted scientific facts.

Each node copies and updates the complete mechanism table. Its cost is O(M)
in registered mechanisms, independently of sealed history length. Historical
node/call/candidate facts retain their existing exact-original addresses.
"""
from __future__ import annotations

import copy
from pathlib import Path
from urllib.parse import quote


VERSION = 1


def key(kind, identity):
    if kind not in {'mechanism', 'ordinal', 'work', 'candidate', 'node', 'call', 'cycle', 'due', 'judgment', 'repair'}:
        raise ValueError('Unknown accepted scientific fact kind')
    if not isinstance(identity, (str, int)) or isinstance(identity, bool):
        raise ValueError('Exact accepted scientific identity required')
    encoded=quote(str(identity),safe='')
    return 'search/' + kind + '/' + '/'.join(encoded[index:index+96] for index in range(0,len(encoded),96))


def empty():
    return dict(version=VERSION, node_count=0, mechanism_count=0, pending_count=0,
        remaining_count=0, owed_count=0, candidate_count=0, relation_count=0,
        failed_candidate_count=0, unknown_candidate_count=0, observation_count=0,
        partial_observation_count=0,
        coverage='unknown', current_candidate=None, a_recommended=None,
        historical_a_recommendation=None, actual_submission=None, latest_a_step=None,
        current_h=None,last_h=None,last_w=None,last_science=None,focus=None, allocation=None,
        table=dict(mechanisms={},ordinals=[],remaining=[],nominations=dict(by_mechanism_ref={},latest_extras=[],source_step=None,origin_identity=None)),current_facts={},extra_grant=None,
        opportunity_unknown_count=0, work_count=0, diagnostics=[],
        pending_work_response=None,
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
    return item['status'] not in {'deferred', 'waived'} and item.get('accepted_calls', 0) < initial_calls


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


def prepare_node(root, job, payload, seal, *, summary, lookup, usage_lookup=None):
    """Copy the small breadth table and apply this accepted original's facts.

    ``lookup`` must read at the same canonical accepted head as ``summary``. The
    returned rows and summary are committed together by accepted_budget, which
    owns duplicate-event and interrupted-publication reconciliation.
    """
    from . import contracts as c, history
    delta = _Delta(summary, lookup)
    current = delta.summary
    step, role = job['step'], job['role']
    if delta.get('node', step) is not None:
        raise ValueError('Scientific original is already accepted at this head')
    settings=c.settings(root, job)
    limits = settings.get('search_policy') or {}
    threshold=(settings.get('portfolio_policy') or {}).get('review_after_cycles')
    prior_threshold=current['portfolio'].get('review_after_cycles')
    if current['node_count'] and prior_threshold!=threshold:
        raise ValueError('Accepted portfolio policy cannot change without an explicit rebuild')
    current['portfolio']['review_after_cycles']=threshold
    initial_calls = limits.get('initial_calls', 1)
    control = seal.get('control_outcome', {})
    valid = all(control.get(field) == 'applied' for field in ('search_status', 'selection_status', 'decision_status'))
    ordinary = not (job.get('formal_review') or job.get('purpose') == 'formal_review' or job.get('engineering_repair'))
    current['node_count'] += 1
    if role=='H':current['last_h']=step
    if role=='W':current['last_w']=step
    current['relation_count'] += len(job.get('direct_refs', [])) + len(seal.get('selected_inputs', []))
    node = dict(step=step, role=role, next_action=payload.get('next_action'),
        stage_intent=payload.get('stage_intent'), focus=copy.deepcopy((job.get('search') or {}).get('focus')),
        allocation=copy.deepcopy((job.get('search') or {}).get('allocation')),
        breadth=bool((job.get('search') or {}).get('breadth')), assessed=False,portfolio_assessed=False,
        direct_steps=[int(row['path'].split('/')[2]) for row in job.get('direct_refs',[])])
    delta.put('node', step, node)
    request=current['pending_work_response']
    if request and step==request['source_a']+1:
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
    if ordinary and role in {'H', 'A'}:
        current['last_science'] = dict(step=step, role=role)
    if ordinary and valid:
        current['focus'] = copy.deepcopy(control.get('resolved_focus'))
        current['allocation'] = copy.deepcopy(control.get('resolved_allocation'))
        current['diagnostics'] = copy.deepcopy(control.get('diagnostics', []))
        if role == 'H':
            current['current_h'] = step
            plan = payload.get('search_plan') or {}
            origin=_origin(root,step)
            for index, authored in enumerate(plan.get('commitments', []), 1):
                ref = authored.get('existing_ref') or f'h{step:03d}.m{index}'
                item = delta.get('mechanism', ref)
                if item is None:
                    item = delta.put('mechanism', ref, dict(status='pending', original_h=step,
                        accepted_calls=0, participated_cycles=0, last_participated_cycle=current['portfolio']['completed_cycle_count']))
                    current['mechanism_count'] += 1
                    item.update(ordinal=current['mechanism_count'],origin_identity=origin,
                        origin_ref=dict(goal_id=origin['goal_id'],source_h=step,mechanism_ref=ref))
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
                item.update(label=authored['label'], minimal_test=authored['minimal_test'], source_h=step,
                    source_origin_identity=origin,source_h_ref=_seal_ref(root,step))
                delta.queue(ref, item, step)
                if current['extra_grant'] and current['extra_grant']['mechanism_ref'] == ref:
                    current['extra_grant'] = None
        elif role == 'A':
            review = payload.get('search_review') or {}
            for authored in review.get('dispositions', []):
                ref = authored['mechanism_ref'];item = delta.get('mechanism', ref)
                if item is None:
                    raise ValueError('Accepted A disposition has no registered mechanism')
                if item['status'] != authored['status']:
                    was_waived=item['status']=='waived'
                    _status(delta, ref, item, authored['status'], initial_calls)
                    if authored['status']=='waived':_due(delta,item,active=False)
                    elif was_waived:_due(delta,item,reset=True)
                    item['disposition'] = dict(reason=authored['reason'], source_a=step)
                    if item['status'] == 'pending' and not any(row['mechanism_ref']==ref for row in current['table']['remaining']):delta.queue(ref,item,step)
            focus = current['focus']
            if focus and isinstance(review.get('next_focus'), dict) and review['next_focus'].get('mechanism_ref') is not None:
                current['extra_grant'] = dict(mechanism_ref=focus['mechanism_ref'], source_a=step,source_a_ref=_seal_ref(root,step),used_calls=0)
    elif ordinary:
        current['focus'] = None
        current['allocation'] = None
        current['diagnostics'] = copy.deepcopy(control.get('diagnostics', []))
    observation = history.read_observation(root, job, seal) if seal.get('observations') else None
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
        call_origin=_origin(root,step) if managed else None
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
    if role == 'A' and payload.get('next_action')!='audit':
        candidate = payload.get('candidate_recommendation')
        # Only an original visible before this A may occupy its authored slot.
        # An unresolved optional citation is retained in the raw original, not
        # promoted to a recommendation or a new scientific execution grant.
        if not isinstance(candidate,str) or not candidate or len(candidate.encode())>160 or lookup(key('candidate',candidate)) is None:candidate=None
        if current['a_recommended'] is not None:
            current['historical_a_recommendation'] = current['a_recommended']
        current['latest_a_step'] = step
        current['a_recommended'] = dict(candidate_id=candidate, author_step=step) if candidate is not None else None
    _portfolio(delta, job, payload, observation, valid and ordinary, initial_calls,usage_lookup)
    _work(delta, job, payload, valid and ordinary)
    if role=='W' and job.get('candidate_nomination_mechanisms'):
        from . import candidate_nominations
        declaration=candidate_nominations._preview(c.scientific_field(job,payload,'candidate_nominations'))
        if declaration is not None:
            nominations=current['table']['nominations'];origin=_origin(root,step)
            nominations.update(source_step=step,origin_identity=origin,
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
                or name.startswith('search/call/') and value['status']=='unknown'):
            selected[name]=copy.deepcopy(value)
    if step is None:
        for name,value in summary['current_facts'].items():
            if name.startswith('search/node/'):selected[name]=copy.deepcopy(value)
    else:retain('node',step)
    if summary['last_science']:retain('node',summary['last_science']['step'])
    portfolio=summary['portfolio'];pending=portfolio['pending_w']
    if pending:
        retain('node',pending['step']);retain('node',pending['cycle_root'])
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


def _portfolio(delta, job, payload, observation, valid, initial_calls,usage_lookup):
    summary, step = delta.summary, job['step']
    portfolio = summary['portfolio']
    if job['role'] == 'W':
        focus = (job.get('search') or {}).get('focus')
        if focus is not None:
            branches = [focus['mechanism_ref']]
            if (job.get('search') or {}).get('breadth'):
                branches = list(dict.fromkeys(row['mechanism_binding']['mechanism_ref'] for row in (observation or {}).get('managed_calls', []) if row.get('mechanism_binding')))
            delta.get('node',step)['branches']=branches
            portfolio['pending_w'] = dict(step=step, branch=focus['mechanism_ref'], branches=branches[:4],branch_count=len(branches),
                completed=payload.get('stage_intent') == 'review', repair=focus['action'] == 'repair',
                cycle_root=job['search']['repair_root'] if focus['action']=='repair' else step)
            if focus['action']=='repair':_exclude_cycle(delta,job['search']['repair_root'])
        return
    if job['role'] != 'A' or not valid:return
    if job.get('author_correction'):
        corrected=delta.get('node',job['author_correction']['root']['step'])
        if corrected and corrected.get('assessed_cycle_root') is not None:_exclude_cycle(delta,corrected['assessed_cycle_root'])
    review = payload.get('portfolio_review')
    if isinstance(review, dict) and 'main_line' in review and payload.get('next_action') != 'wait' and (job.get('portfolio_context') or {}).get('required_for_completed_w', True):
        main = review['main_line']
        root_step=(job.get('author_correction') or {}).get('root',{}).get('step',step)
        judgment=delta.get('judgment',root_step)
        if judgment is None:
            judgment=delta.put('judgment',root_step,dict(previous_main=portfolio['main_line'],previous_streak=portfolio['mainline_streak'],previous_root=portfolio['latest_judgment_root']))
            portfolio['latest_judgment_root']=root_step
        judgment.update(main_line=main,source_step=step)
        suffix=[];cursor=portfolio['latest_judgment_root']
        # Only an explicit replacement of an older judgment reads its accepted
        # suffix. Ordinary append/latest clarification keeps scalar updates.
        while cursor!=root_step:
            item=delta.get('judgment',cursor)
            if item is None:raise ValueError('Accepted portfolio judgment suffix is missing')
            suffix.append(item);cursor=item['previous_root']
            if cursor is None:raise ValueError('Corrected portfolio judgment is outside its accepted order')
        previous_main,streak=judgment['previous_main'],judgment['previous_streak']
        for item in [judgment,*reversed(suffix)]:
            item['previous_main'],item['previous_streak']=previous_main,streak
            selected=item['main_line']
            streak=streak+1 if selected is not None and selected==previous_main else int(selected is not None)
            previous_main=selected
        latest=suffix[0] if suffix else judgment
        portfolio['main_line']=previous_main;portfolio['mainline_streak']=streak
        portfolio['mainline_source']=dict(source_step=latest['source_step'],pointer='/portfolio_review/main_line')
    pending = portfolio['pending_w'];focus = summary['focus']
    pending_node=delta.get('node',pending['step']) if pending else None
    branches=pending_node.get('branches',[pending['branch']]) if pending_node else []
    final = job.get('final_report') or job.get('final_only') or job.get('purpose') == 'closeout'
    direct = bool(pending and any(row['path'] == f'inputs/parents/{pending["step"]:03d}/conclusion.json' for row in job.get('direct_refs', [])))
    original_cycle=True
    if job.get('author_correction'):
        original=delta.get('node',job['author_correction']['root']['step'])
        original_cycle=bool(original and original['role']=='A' and pending and pending['step'] in original['direct_steps'])
    repairing = focus and focus['action'] == 'repair' and pending and focus['mechanism_ref'] in branches
    if original_cycle and pending and pending['completed'] and not repairing and (direct or final) and payload.get('next_action') not in {'audit', 'formal_review', 'extract', 'wait'}:
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
                w_usage,a_usage=usage_lookup(pending['step']),usage_lookup(step)
                sample_complete=bool(w_usage and a_usage and w_usage['complete'] and a_usage['complete'] and not pending['repair'] and not job.get('author_correction'))
                if sample_complete:reported=w_usage['reported_tokens']+a_usage['reported_tokens']
            sample=dict(w_step=pending['step'],a_step=step,reported_tokens=reported)
            advice=portfolio['token_advice']
            advice['complete_sample_count']+=int(sample_complete)
            advice['excluded_sample_count']+=int(not sample_complete)
            if sample_complete:
                advice['reported_tokens_sum']+=reported
                advice['samples']=(advice['samples']+[sample])[-2:]
            delta.put('cycle', pending['cycle_root'], dict(w_step=pending['step'],a_step=step, mechanism_refs=branches,
                repair=pending['repair'],sample_complete=sample_complete,reported_tokens=reported))
            delta.get('node',step)['assessed_cycle_root']=pending['cycle_root']
        if not old['assessed']:
            old['assessed']=True
            if old['breadth']:
                for authored in (payload.get('search_review') or {}).get('dispositions', []):
                    if authored['status'] in {'screened', 'deferred', 'waived'}:
                        item = delta.get('mechanism', authored['mechanism_ref'])
                        delta.unqueue(authored['mechanism_ref'], item)
            elif old.get('allocation'):
                ref = old['allocation']['mechanism_ref'];item = delta.get('mechanism', ref)
                if item and item.get('planned_step') == old['allocation']['planned_step']:delta.unqueue(ref, item)
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
    if not valid or job['role'] != 'A':return
    review = payload.get('portfolio_review')
    if not isinstance(review, dict):return
    executed_refs=[]
    for update in review.get('work_updates', []):
        item = delta.get('work', update['work_ref'])
        if item is None:raise ValueError('Accepted scientific work identity is absent')
        was_active = item['status'] != 'withdrawn'
        item.update(status={'withdraw':'withdrawn','execute':'executed','defer':'deferred'}[update['action']],
            updates=[dict(source_a=job['step'], **update)])
        if update['action'] == 'execute':
            item['executions'] = [dict(source_a=job['step'], response=None)]
            executed_refs.append(update['work_ref'])
        delta.summary['work_count'] += int(item['status'] != 'withdrawn') - int(was_active)
    work = review.get('discriminating_work')
    if isinstance(work, dict) and work.get('existing_ref') is None:
        ref = f'A{job["step"]:03d}-D001'
        executed = work['next_action'] == payload.get('next_action')
        delta.put('work', ref, dict(work_ref=ref, source_a=job['step'],
            declaration_pointer='/portfolio_review/discriminating_work',
            mechanism_refs=copy.deepcopy(work['mechanism_refs']),question=work['question'],next_action=work['next_action'],
            change_criterion=work.get('change_criterion') if isinstance(work.get('change_criterion'),str) else work['question'],
            status='executed' if executed else 'deferred',
            updates=[dict(source_a=job['step'],action='execute' if executed else 'defer',reason=review.get('reason',''),deferral=copy.deepcopy(review.get('deferral')))],
            executions=[dict(source_a=job['step'], response=None)] if executed else []))
        delta.summary['work_count'] += 1
        if executed:executed_refs.append(ref)
    delta.get('node',job['step'])['executed_work']=executed_refs
    if executed_refs:delta.summary['pending_work_response']=dict(source_a=job['step'])


def prepare_recovery(*, summary, lookup, step, call_id, resolved,recovery_ref):
    """Reconcile one already consumed original call; never create an opportunity.

    The caller owns original-call evidence qualification. A recovery can supply
    terminal knowledge for this exact identity, but cannot change its binding or
    erase its original contribution to accepted-call counters.
    """
    delta = _Delta(summary, lookup)
    identity = f'{step}:{call_id}'
    original = delta.get('call', identity)
    if original is None:
        raise ValueError('Recovery requires its accepted original scientific call')
    if (resolved.get('call_id') != call_id
            or resolved.get('mechanism_binding') != original['mechanism_binding']
            or resolved.get('status') not in {'known_success', 'known_failed', 'unknown'}):
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
    review = obj(payload.get('search_review'))
    for row in rows(review.get('dispositions')):
        if isinstance(row, dict) and isinstance(row.get('mechanism_ref'), str):mechanisms.add(row['mechanism_ref'])
    focus = review.get('next_focus')
    if isinstance(focus, dict) and isinstance(focus.get('mechanism_ref'), str):mechanisms.add(focus['mechanism_ref'])
    portfolio = obj(payload.get('portfolio_review'))
    if isinstance(portfolio, dict):
        if isinstance(portfolio.get('main_line'), str):mechanisms.add(portfolio['main_line'])
        work = portfolio.get('discriminating_work')
        if isinstance(work, dict):
            mechanisms.update(ref for ref in rows(work.get('mechanism_refs')) if isinstance(ref, str))
            if isinstance(work.get('existing_ref'), str):works.add(work['existing_ref'])
        for row in rows(portfolio.get('work_updates')):
            if isinstance(row, dict) and isinstance(row.get('work_ref'), str):works.add(row['work_ref'])
    for row in rows(payload.get('final_coverage')):
        if isinstance(row, dict) and isinstance(row.get('mechanism_ref'), str):mechanisms.add(row['mechanism_ref'])
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
    source=dict(origin_identity=item['source_origin_identity'],seal_ref=item['source_h_ref'])
    return ref,item,head,source
