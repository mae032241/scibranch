"""Frozen W slices and scientific commitments derived from original nodes."""
from __future__ import annotations

from . import configuration_updates

from . import execution

import json
import copy
import re

from pathlib import Path

from . import budget, contracts as c, history, originals

DEFAULT_POLICY = dict(max_w_seconds=900.0, max_w_observed_tokens=2_000_000, max_requested_repairs=2)
MAX_VIEW_BYTES = 4096
MODES = ('screen', 'improve', 'repair', 'validate')


def mechanism_producer(job):
    return job['role'] in {'H','W'}


def completed_worker(job, payload):
    return payload.get('stage_intent') == 'decision'


class UnresolvedSearch(ValueError):
    def __init__(self, code, subject, message, *, expected=None):
        super().__init__(message)
        self.code, self.subject = code, subject
        self.expected = expected


class UnresolvedScientificReference(ValueError):
    """An optional authored citation cannot resolve within validated originals."""


def policy(value):
    if not isinstance(value, dict) or set(value) not in (set(DEFAULT_POLICY),set(DEFAULT_POLICY)|{'initial_calls'}):
        raise ValueError('Search policy requires finite ceilings and optional initial_calls')
    seconds = value['max_w_seconds']
    if type(seconds) not in (int, float) or not 0 < seconds < float('inf'):
        raise ValueError('W slice seconds must be positive and finite')
    if any(type(value[key]) is not int or value[key] <= 0
           for key in ('max_w_observed_tokens', 'max_requested_repairs')):
        raise ValueError('W tokens and repair ceilings must be positive integers')
    if 'initial_calls' in value and (type(value['initial_calls']) is not int or value['initial_calls'] <= 0):
        raise ValueError('Initial registered-call opportunities must be a positive integer')
    return dict(value)


def _frozen_sources(root,job,*,source_rows=None):
    """Resolve this exact task's declared prefix, never a latest-instance view."""
    prepared=None if source_rows is None else {row[0]['step']:row for row in source_rows}
    rows={}
    for ref in job.get('history',{}).get('nodes',[]):
        step=ref['step']
        if type(step) is not int or not 0<step<job['step'] or step in rows:
            raise ValueError('Scientific reference prefix identity changed')
        row=history._source(root,step) if prepared is None else prepared.get(step)
        if row is None:raise ValueError('Prepared scientific prefix omitted an original')
        if row[3]!=ref['seal_ref']:raise ValueError('Scientific reference original changed')
        rows[step]=row
    for source,payload,seal in _direct_assessment_sources(root,job,prepared):
        step=source['step']
        row=history._source(root,step) if prepared is None else prepared[step]
        if step in rows and rows[step][2]!=seal:raise ValueError('Scientific direct prefix changed')
        rows[step]=row
    return rows


def _direct_assessment_sources(root,job,prepared=None):
    if prepared is None:return history.direct_sources(root,job)
    # During lineage construction no logical parent aliases have been published.
    # Its typed rows already come from history.source_sequence/_source; bind the
    # exact raw originals used by build_job, rather than resolving future aliases.
    result=[];seen=set()
    for ref in job.get('direct_originals',job['direct_refs']):
        match=re.fullmatch(r'inputs/parents/([0-9]{3,})/conclusion\.json',ref['path'])
        step=int(match[1]) if match is not None else None
        row=prepared.get(step)
        if (row is None or step in seen or not 0<step<job['step'] or match[1]!=f'{step:03d}'
                or ref['size_bytes']!=row[2]['raw_ref']['size_bytes']):
            raise ValueError('Prepared direct scientific original changed')
        seen.add(step);result.append(row[:3])
    return result


def assessment_context(root,job,*,state=None,source_rows=None):
    """Shared B1/FINAL identity facts; this does not define a B2 cycle."""
    empty=dict(applicable=False,mechanisms={},completed_w=[],discriminating_work=[],sources={})
    if job['role']!='A' or job.get('formal_review') or job.get('purpose') in {'formal_review','worker_consultation'}:
        return empty
    policy=c.settings(root,job)
    if policy.get('search_policy') is None:
        return empty
    if state is None:
        state=current_state(root,job)
    return _assessment_context_for_policy(root,job,policy=policy,state=state,source_rows=source_rows)


def _assessment_context_for_policy(root,job,*,policy,state,source_rows=None):
    """Use an already selected policy and state while resolving exact sources."""
    empty=dict(applicable=False,mechanisms={},completed_w=[],discriminating_work=[],sources={})
    if policy is None or state is None:
        raise ValueError('Assessment context requires its produced policy and state')
    if job['role']!='A' or job.get('formal_review') or job.get('purpose') in {'formal_review','worker_consultation'} or policy.get('search_policy') is None:
        return empty
    sources=_frozen_sources(root,job,source_rows=source_rows)
    prepared=None if source_rows is None else {row[0]['step']:row for row in source_rows}
    direct={source['step'] for source,_,_ in _direct_assessment_sources(root,job,prepared)}
    final=bool(job.get('final_report') or job.get('purpose')=='closeout' or job.get('final_only'))
    pending=(state.get('portfolio') or {}).get('pending_w')
    if final and pending and pending['step'] not in sources:
        sources[pending['step']]=history._source(root,pending['step'])
    eligible=[row for step,row in sources.items() if row[0]['role']=='W' and completed_worker(row[0],row[1])
        and (step in direct or final)]
    # A final summary without a direct W can assess only the still-pending
    # original inside its own cut. Already assessed history requires coverage,
    # not another invented portfolio judgment.
    if final and not any(row[0]['step'] in direct for row in eligible):
        pending=(state.get('portfolio') or {}).get('pending_w')
        eligible=[row for row in eligible if pending is not None and row[0]['step']==pending['step']]
    completed=[dict(step=row[0]['step'],limitations=copy.deepcopy(row[1].get('limitations',[])),
        delivery=copy.deepcopy(row[2].get('control_outcome',{}).get('worker_delivery'))) for row in eligible]
    if eligible and pending:
        chain=pending.get('steps',[pending['step']])
        if any(row[0]['step']==pending['step'] for row in eligible):
            completed=[]
            for member in chain:
                if state.get('_node_get') is not None:
                    fact=state['_node_get'](member)
                    if fact is None or fact['role']!='W':raise ValueError('Pending assessment chain original is absent')
                    completed.append(dict(step=member,limitations=copy.deepcopy(fact.get('limitations',[])),delivery=copy.deepcopy(fact.get('delivery'))))
                else:
                    row=sources.get(member)
                    if row is None:raise ValueError('Pending assessment chain original is absent')
                    completed.append(dict(step=member,limitations=copy.deepcopy(row[1].get('limitations',[])),
                        delivery=copy.deepcopy(row[2].get('control_outcome',{}).get('worker_delivery'))))
    result=dict(applicable=bool(eligible and state.get('summary',{}).get('mechanism_count',len(state['mechanisms']))),mechanisms=copy.deepcopy(state['mechanisms']),
        completed_w=completed,discriminating_work=copy.deepcopy(state['order'].get('discriminating_work',[])),sources=sources,final=final)
    frozen=job.get('portfolio_context')
    if source_rows is None and frozen is not None and (
            frozen['assessed_w']!=[dict(source_step=row['step'],pointer='/scientific_result') for row in result['completed_w']]
            or frozen['active_work_count']!=state.get('summary',{}).get('work_count',sum(row['status']!='withdrawn' for row in result['discriminating_work']))):
        raise ValueError('Portfolio context differs from its frozen scientific originals')
    return result


def validate_science_refs(root,job,refs):
    """Check identity and author-visible fields only, never scientific merit."""
    if not refs:return []
    sources={}
    for ref in refs:
        if not isinstance(ref,dict) or not {'source_step','pointer'}<=ref.keys() or type(ref['source_step']) is not int:
            raise UnresolvedScientificReference('Scientific evidence needs source_step and RFC6901 pointer')
        step=ref['source_step']
        if step not in sources:
            from . import accepted_prefix
            try:
                accepted_prefix.member(root,accepted_prefix.for_job(job),step)
                sources[step]=history._source(root,step)
            except (ValueError,FileNotFoundError):
                raise UnresolvedScientificReference('Scientific evidence is outside the frozen visible prefix') from None
        row=sources.get(step);pointer=ref['pointer']
        if row is None or not isinstance(pointer,str) or not pointer.startswith('/') or re.search(r'~(?![01])',pointer):
            raise UnresolvedScientificReference('Scientific evidence is outside the frozen visible prefix')
        source,payload,_,_=row
        keys=[key.replace('~1','/').replace('~0','~') for key in pointer[1:].split('/')]
        if keys[0] not in payload or keys[0] in {'role','direct_refs','implementation_selection','control_outcome'}:
            raise UnresolvedScientificReference('Scientific evidence points outside declared scientific fields')
        value=payload
        for key in keys:
            if isinstance(value,dict) and key in value:value=value[key]
            elif isinstance(value,list) and re.fullmatch(r'0|[1-9][0-9]*',key) and int(key)<len(value):value=value[int(key)]
            else:raise UnresolvedScientificReference('Scientific evidence pointer does not resolve')
    return copy.deepcopy(refs)


def _evidence_refs(value):
    """Only explicit exact scientific references are consumed from optional detail."""
    if not isinstance(value,list):return []
    return [ref for ref in value if isinstance(ref,dict) and 'source_step' in ref and 'pointer' in ref]


def require_portfolio_review(root,job,payload,*,context=None,interpretation=None):
    """Return optional authored comparison detail without a completeness gate."""
    return payload.get('portfolio_review')


def optional_reference_diagnostics(root,job,payload):
    """Unconsumed planning advice does not authorize work or require repair."""
    return []


def schema(role, protocol=None):
    if role not in {'H','W','A'}:raise ValueError('Only H, W and A declare scientific search choices')
    return dict(type=['object','null'],description=(
        'Optional high-level method changes and the next W focus. H and W use commitments (label, optional existing_ref) and focus (index, action): '
        'index is a 1-based integer in this payload\'s commitments, or null for the retained breadth screen. Omissions retain earlier mechanisms. '
        'A optional search_review is retained as authored advice and does not select focus, dispositions or execution opportunities. '
        'Actions: screen, improve, repair, validate. Scientific comparison and remaining gaps belong in scientific_result and limitations.'))


def _managed_refs(root,source,seal,state):
    if not seal.get('observations'):return set()
    observation=history.read_observation(root,source,seal);hashes={};refs=set()
    for row in observation.get('managed_calls',[]):
        binding=row.get('mechanism_binding')
        if binding is None:continue
        ref=binding['mechanism_ref']
        current=state['mechanisms'].get(ref)
        if current is None or row['status'] not in {'known_success','known_failed'} or any(binding[key]!=current[key] for key in ('source_step','source_role')):continue
        step=current['source_step']
        if step not in hashes:hashes[step]=history.source_seal_ref(originals.node(root,step))
        if binding['source_ref']==hashes[step]:refs.add(ref)
    return refs


def _screen_refs(root,source,seal,state,*,science):
    """A judges this final W's findings using its frozen, reusable observations.

    Reused proofs qualify scientific assessment only. They remain absent from
    this W's managed_calls and its new execution or portfolio investment counts.
    """
    from . import worker_delivery
    if not worker_delivery.applicable(source):return _managed_refs(root,source,seal,state)
    owner=originals.node(root,source['step'])
    facts=worker_delivery.validate_original(root,owner,source,seal)
    # The sealed producer already checked every retained observation against
    # this frozen task's producer bindings. Compare those supplied bindings with the
    # current accepted state instead of reopening the original call records.
    targets={row['mechanism_ref'] for row in source['search']['breadth']['mechanisms']
        if (current:=state['mechanisms'].get(row['mechanism_ref'])) is not None
        and all(row[key]==current[key] for key in ('source_step','source_ref','source_role'))}
    evidence=[row for row in facts['evidence'] if row['mechanism_ref'] in targets]
    return worker_delivery.finding_coverage(science,evidence,targets)['observed']


def _mechanism_sources(root,job,ref,state,*,screen=False):
    if screen:
        if state.get('assessment_workers') is None:
            raise ValueError('Scientific screening requires produced assessment Worker facts')
        from . import worker_delivery
        current=state['mechanisms'][ref]
        sources={};observations={}
        for source in state['assessment_workers']:
            for evidence in source['screen_observations']:
                binding=evidence['mechanism_binding']
                if binding['mechanism_ref']!=ref or any(binding[key]!=current[key]
                        for key in ('source_step','source_ref','source_role')):continue
                proof=copy.deepcopy(evidence['accepted'])
                # Local proofs belong to their accepted source branch. Fork
                # projections make that same origin explicit; normalize only
                # this in-memory comparison, never the retained original fact.
                if 'origin_identity' not in proof:proof['origin_identity']=source['origin_identity']
                normalized=dict(evidence,accepted=proof)
                identity=worker_delivery._evidence_key(normalized)
                previous=observations.setdefault(identity,normalized)
                if previous!=normalized:raise ValueError('Accepted screen observation identity changed')
                if identity in sources:continue
                sources[identity]=source
        # Multiple W declarations of the same original observation do not
        # invent independent sources. Distinct originating W contributions
        # remain explicit; no chronology or latest-result heuristic selects one.
        authors={source['step']:source for source in sources.values()}
        return list(authors.values())
    sources=[]
    for source,payload,seal in history.direct_sources(root,job):
        if source['role']!='W':continue
        if (source.get('search') or {}).get('breadth'):
            refs=_screen_refs(root,source,seal,state,science=payload) if screen else _managed_refs(root,source,seal,state)
            if ref in refs:sources.append(source)
        elif ((source.get('search') or {}).get('focus') or {}).get('mechanism_ref')==ref and (not screen or source['search']['focus']['action']=='screen'):
            sources.append(source)
    return sources



def _repair_subject(root,job,ref,*,selected_inputs):
    """Bind one old known observation; current mechanism authority is a separate identity.

    Direct W inputs are eligible. An existing exact implementation selection may
    name an older W and disambiguate its accepted input bytes, never its outputs.
    The owner supplies accepted selection rows; repair does not select them again.
    No labels, chronology or current-H qualification select an observation.
    """
    direct={source['step']:(source,seal) for source,_,seal in history.direct_sources(root,job) if source['role']=='W'}
    sources=dict(direct);inputs={}
    for item in selected_inputs:
        step=item['origin_step']
        source,_,seal,_=history._source(root,step)
        if source['role']!='W':continue
        sources[step]=(source,seal)
        inputs.setdefault(step,[]).append(item['material'])
    candidates=[];chosen=[]
    for step,(source,seal) in sources.items():
        if not seal.get('observations'):continue
        observation=history.read_observation(root,source,seal)
        for row in observation.get('managed_calls',[]):
            binding=row.get('mechanism_binding')
            if binding is None or binding['mechanism_ref']!=ref:continue
            accepted=c.read(originals.resolve_ref(originals.node(root,step),dict(row['accepted'],path=row['accepted']['snapshot'])))
            input_originals=[dict(accepted['script'],path=accepted['source_script']),*accepted.get('snapshot_files',[])]
            # Archive objects deduplicate bytes across distinct logical outputs.
            # Only the selected producer's original artifact path can prove this
            # input identity; an archive-path alias cannot disambiguate a call.
            prefix=f'inputs/parents/{step:03d}/artifacts/'
            explicit=any(material['source']==str(originals.node(root,step).logical_workspace/'artifacts'/material['path'][len(prefix):])
                and material['source']==original['path']
                and all(material[key]==original[key] for key in ('size_bytes',))
                for material in inputs.get(step,[]) for original in input_originals)
            candidate=(source,seal,row)
            if step in direct:candidates.append(candidate)
            if explicit:chosen.append(candidate)
    candidates=chosen or candidates
    if not candidates:return dict(status='unavailable' if sources else 'not_direct',root=None,source=None)
    if any(row['status']=='unknown' for _,_,row in candidates):return dict(status='unknown',root=None,source=None)
    known=[item for item in candidates if item[2]['status'] in {'known_success','known_failed'}]
    if len(known)!=1:return dict(status='ambiguous' if known else 'unavailable',root=None,source=None)
    source,seal,row=known[0];binding=row['mechanism_binding'];old_producer=binding['source_step']
    # Validate the old observation under its OWN producer seal, even after a revision.
    original_producer,_,_,sha=history._source(root,old_producer)
    if not mechanism_producer(original_producer) or binding['source_role']!=original_producer['role'] or binding['source_ref']!=sha:
        return dict(status='version_mismatch',root=None,source=None)
    identity=originals.source_record(originals.node(root,source['step']),'result-identity.json')
    subject=dict(step=source['step'],job_ref=seal['job_ref'],raw_ref=seal['raw_ref'],
        seal_ref=c.record_ref(identity),call=copy.deepcopy(row))
    return dict(status='available',root=source['search'].get('repair_root') or source['step'],source=subject)


def _repair_choice(root,job,ref,*,selected_inputs):
    try:return _repair_subject(root,job,ref,selected_inputs=selected_inputs)
    except FileNotFoundError:
        return dict(status='missing_original',root=None,source=None)
    except ValueError as error:
        if str(error)!='Expected an original regular file':raise
        return dict(status='missing_original',root=None,source=None)


def _repair_diagnostic(state,choice,*,policy):
    if choice['status']!='available':return 'search_repair_source_'+choice['status']
    count=state['_repair_get'](choice['root']) if state.get('summary') is not None else state['repairs'].get(choice['root'],0)
    if count>=policy['search_policy']['max_requested_repairs']:
        return 'search_repair_limit'
    return None


def _assessed(root,job,state):
    if job['role']!='A' or job.get('purpose') is not None:return None
    original=history._source(root,job['author_correction']['root']['step'])[0] if job.get('author_correction') else job
    if original['role']!='A':return None
    sources=[(source,seal) for source,_,seal in history.direct_sources(root,job) if source['role']=='W']
    if len(sources)!=1:return None
    source,source_seal=sources[0]
    if not any(ref['path']==f'inputs/parents/{source["step"]:03d}/conclusion.json' for ref in original['direct_refs']):return None
    focus=(source.get('search') or {}).get('focus')
    if focus is None:return None
    seal=originals.source_record(originals.node(root,source['step']),'result-identity.json')
    return dict(identity=(source['step'],c.record_path(seal)),mechanism_ref=focus['mechanism_ref'],allocation=source['search']['allocation'],
        breadth=bool(source['search'].get('breadth')),mechanism_refs=sorted(_managed_refs(root,source,source_seal,state)))


def minimal_test_text(value):
    """Display an accepted scientific description without changing its original."""
    return value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,allow_nan=False)


def _choice(job,payload):
    """Read only the mechanism and route keys consumed by this controller."""
    role=job['role']
    if role=='A':return None
    if role not in {'H','A'} and not mechanism_producer(job):return None
    field='search_plan' if mechanism_producer(job) else 'search_review'
    value=c.scientific_field(job,payload,field)
    if value is None:return None
    def unresolved(subject,expected=None):
        if subject in {'focus','next_focus'}:
            from .scientific_intent import focus_diagnostic
            row=focus_diagnostic(job,code='science_choice_unresolved')
            raise UnresolvedSearch(row['code'],row['subject'],row['message'],expected=row['expected'])
        message=f'Correct {subject}: expected {expected}' if expected else 'Supply the mechanism or route keys needed by this choice'
        raise UnresolvedSearch('science_choice_unresolved',subject,message,expected=expected)
    if not isinstance(value,dict):unresolved(field)
    result={}
    if mechanism_producer(job):
        scope=value.get('scope')
        if scope is not None and not isinstance(scope,str):unresolved('scope')
        result['scope']=scope
        rows=value.get('commitments',[])
        if not isinstance(rows,list):unresolved('search_plan.commitments','an array of commitment objects')
        result['commitments']=[]
        for index,row in enumerate(rows):
            path=f'search_plan.commitments[{index}]'
            if not isinstance(row,dict):unresolved(path,'an object')
            if not isinstance(row.get('label'),str):unresolved(path+'.label','a string')
            if not row['label'].strip():
                expected='a nonblank string distinct within commitments'
                raise UnresolvedSearch('h_mechanism_unresolved',path+'.label',
                    f'Correct {path}.label: expected {expected}',expected=expected)
            minimal=row.get('minimal_test',row['label'])
            expected='a nonblank string or nonempty JSON object/array'
            if not isinstance(minimal,(str,dict,list)):unresolved(path+'.minimal_test',expected)
            if not minimal or isinstance(minimal,str) and not minimal.strip():
                raise UnresolvedSearch('h_mechanism_unresolved',path+'.minimal_test',
                    f'Correct {path}.minimal_test: expected {expected}',expected=expected)
            existing=row.get('existing_ref')
            if existing is not None and not isinstance(existing,str):unresolved(path+'.existing_ref','a string or null')
            result['commitments'].append(dict(label=row['label'],minimal_test=minimal_test_text(minimal),existing_ref=existing))
    key='focus' if mechanism_producer(job) else 'next_focus';name='index' if mechanism_producer(job) else 'mechanism_ref'
    focus=value.get(key)
    if focus is not None:
        if (not isinstance(focus,dict) or not isinstance(focus.get('action'),str) or focus['action'] not in MODES
                or name not in focus or 'label' in focus):unresolved(key)
        selected=focus[name]
        if mechanism_producer(job):
            if selected is not None and (type(selected) is not int or not 1<=selected<=len(result['commitments'])):unresolved(key)
        elif selected is not None and not isinstance(selected,str):unresolved(key)
        focus={name:selected,'action':focus['action']}
    result[key]=focus
    return result


def _apply_science(root,job,payload,state,diagnostics,*,policy,selected_inputs):
    mechanisms=state['mechanisms']
    if job['role']=='A':return
    if mechanism_producer(job):
        plan=_choice(job,payload)
        if plan is not None:
            if job['role']=='H' and (plan['commitments'] or job.get('purpose')!='worker_consultation'):state['current_h']=job['step']
            labels=set();refs=set()
            for index,item in enumerate(plan['commitments'],1):
                if not item['label'].strip() or item['label'] in labels:
                    path=f'search_plan.commitments[{index-1}].label'
                    expected='a nonblank string distinct within commitments'
                    raise UnresolvedSearch('h_mechanism_unresolved',path,
                        f'Correct {path}: expected {expected}',expected=expected)
                labels.add(item['label']);ref=item['existing_ref']
                if ref is not None and ref not in mechanisms:raise UnresolvedSearch('h_mechanism_unresolved',ref,'Revision requires an exact registered mechanism')
                ref=ref or f'{job["role"].lower()}{job["step"]:03d}.m{index}'
                if ref in refs:raise UnresolvedSearch('h_mechanism_unresolved',ref,'A revision cannot repeat one mechanism')
                refs.add(ref)
                if ref not in mechanisms:
                    mechanisms[ref]=dict(status='pending',original_step=job['step'],original_role=job['role'])
                    state['order']['remaining'].append(dict(mechanism_ref=ref,planned_step=job['step']))
                else:
                    mechanisms[ref]['status']='pending'
                    mechanisms[ref].pop('disposition',None)
                    remaining=state['order']['remaining']
                    match=next((row for row in remaining if row['mechanism_ref']==ref),None)
                    if match is None:remaining.append(dict(mechanism_ref=ref,planned_step=job['step']))
                    else:match['planned_step']=job['step']
                    if state.get('extra_grant',{} ) and state['extra_grant']['mechanism_ref']==ref:state['extra_grant']=None
                mechanisms[ref].update(label=item['label'],minimal_test=item['minimal_test'],source_step=job['step'],source_role=job['role'])
            if plan.get('scope') is not None:state['scope']=plan['scope']
            state['plan']=plan;state['authorized']|=refs
    from .scientific_intent import resolve_intent
    intent=dict(payload)
    for field in ('search_plan','search_review'):intent[field]=None
    if mechanism_producer(job):intent['search_plan']=_choice(job,payload)
    interpretation=resolve_intent(state,job,intent)
    diagnostics.extend(interpretation['diagnostics'])
    focus=interpretation['focus']
    if mechanism_producer(job) and focus and focus['action']=='repair' and not diagnostics:
        choice=_repair_choice(root,job,focus['mechanism_ref'],selected_inputs=selected_inputs)
        code=_repair_diagnostic(state,choice,policy=policy)
        if code:diagnostics.append(dict(code=code,subject=focus['mechanism_ref']))
    state['focus']=focus
    state['order'].update(remaining=interpretation['unfinished_plan'],seen_w=interpretation['seen_w'],allocation=interpretation['allocation'],diagnostics=diagnostics,
        discriminating_work=interpretation['discriminating_work'])


def interpret(root,job,payload,*,state=None):
    """One prospective interpretation; callers do not publish its mutations."""
    state=current_state(root,job,payload) if state is None else state
    if job.get('purpose')=='formal_review' or job.get('formal_review') or job.get('engineering_repair'):
        return dict(state=copy.deepcopy(state),focus=None,allocation=None,diagnostics=[])
    if (job.get('purpose')=='closeout' or job.get('final_report')) and job['role']!='A':
        return dict(state=copy.deepcopy(state),focus=None,allocation=None,diagnostics=[])
    policy=c.settings(root,job)
    selected=[]
    if mechanism_producer(job):
        choice=c.scientific_field(job,payload,'search_plan' if mechanism_producer(job) else 'search_review')
        focus=choice.get('focus' if mechanism_producer(job) else 'next_focus') if isinstance(choice,dict) else None
        if isinstance(focus,dict) and focus.get('action')=='repair':
            selected=c._accept_selected_inputs(c.workspace(root,job['step']),payload,job,_policy=policy)
    return _interpret_from_facts(root,job,payload,policy=policy,state=state,selected_inputs=selected)


def _interpret_from_facts(root,job,payload,*,policy,state,selected_inputs):
    """Consume the owner's policy, state and already accepted selection."""
    if policy is None or state is None:
        raise ValueError('Scientific interpretation requires its produced policy and state')
    state=copy.deepcopy(state)
    if job.get('purpose')=='formal_review' or job.get('formal_review') or job.get('engineering_repair'):
        return dict(state=state,focus=None,allocation=None,diagnostics=[])
    if job['role']=='A':
        # A assesses the requested claim; W/H retain their authored direction.
        return dict(state=state,focus=state['focus'],allocation=state['order']['allocation'],diagnostics=[])
    if job.get('purpose')=='closeout' or job.get('final_report'):
        return dict(state=state,focus=None,allocation=None,diagnostics=[])
    diagnostics=[]
    field='search_plan' if mechanism_producer(job) else 'search_review' if job['role']=='A' else None
    if field is not None and payload.get(field) is not None and c.scientific_field(job,payload,field) is None:
        diagnostics.extend(row for row in c.scientific_diagnostics(job,payload) if row['subject']==field)
    try:_apply_science(root,job,payload,state,diagnostics,policy=policy,selected_inputs=selected_inputs)
    except UnresolvedSearch as error:
        diagnostic=dict(code=error.code,subject=error.subject)
        if error.expected is not None:diagnostic.update(expected=error.expected,message=str(error))
        diagnostics.append(diagnostic)
    return dict(state=state,focus=state['focus'],allocation=state['order']['allocation'],diagnostics=diagnostics)


def assess_payload(root,job,payload):
    return interpret(root,job,payload)['diagnostics']


def _sealed_portfolio_applicable(root,job,state):
    frozen=job.get('portfolio_context')
    if frozen is not None:
        return frozen['required_for_completed_w']
    if job['role']!='A' or job.get('formal_review') or job.get('purpose') in {'formal_review','worker_consultation'}:return False
    final=bool(job.get('final_report') or job.get('purpose')=='closeout' or job.get('final_only'))
    direct={int(Path(ref['path']).parts[2]) for ref in job['direct_refs']}
    eligible=[]
    for receipt in job.get('history',{}).get('nodes',[]):
        source,payload,_,_=history._source(root,receipt['step'])
        if source['role']=='W' and completed_worker(source,payload) and (source['step'] in direct or final):
            eligible.append(source['step'])
    if final and not any(step in direct for step in eligible):
        pending=state['portfolio']['pending_w']
        eligible=[step for step in eligible if pending and step==pending['step']]
    return bool(eligible and state['mechanisms'])


def _sealed_assessed(root,job,state):
    if job['role']!='A' or job.get('purpose') is not None:return None
    original=history._source(root,job['author_correction']['root']['step'])[0] if job.get('author_correction') else job
    if original['role']!='A':return None
    rows=[history._source(root,int(Path(ref['path']).parts[2])) for ref in job['direct_refs']]
    rows=[row for row in rows if row[0]['role']=='W']
    if len(rows)!=1:return None
    source,_,seal,_=rows[0];step=source['step']
    if not any(ref['path']==f'inputs/parents/{step:03d}/conclusion.json' for ref in original['direct_refs']):return None
    focus=(source.get('search') or {}).get('focus')
    if focus is None:return None
    owner=originals.node(root,step)
    identity=(step,str(originals.sealed_path(owner,dict(path=f'native-evidence/{step:03d}/result-identity.json'))))
    return dict(identity=identity,mechanism_ref=focus['mechanism_ref'],allocation=source['search']['allocation'],
        breadth=bool(source['search'].get('breadth')),mechanism_refs=sorted(_managed_refs(root,source,seal,state)))


def apply_source(root,job,payload,seal,state):
    """Reduce accepted sealed choices; never reinterpret or requalify history."""
    receipt=seal['control_outcome']
    if job.get('purpose')=='formal_review' or job.get('formal_review') or job.get('engineering_repair'):
        return
    if job['role']=='A':return
    valid=all(receipt.get(key)=='applied' for key in ('search_status','selection_status','decision_status'))
    if valid:
        if job.get('purpose')=='closeout' or job.get('final_report'):
            from .scientific_intent import resolve_discriminating_work
            if _sealed_portfolio_applicable(root,job,state):
                state['order']['discriminating_work']=resolve_discriminating_work(state,job,payload)
            state['focus']=copy.deepcopy(receipt['resolved_focus'])
            state['order']['allocation']=copy.deepcopy(receipt['resolved_allocation'])
            state['order']['diagnostics']=copy.deepcopy(receipt.get('diagnostics',[]))
            return
        mechanisms=state['mechanisms'];remaining=state['order']['remaining']
        if mechanism_producer(job) and isinstance(payload.get('search_plan'),dict):
            plan=payload['search_plan']
            if job['role']=='H':state['current_h']=job['step']
            for index,item in enumerate(plan.get('commitments',[]),1):
                ref=item.get('existing_ref') or f'{job["role"].lower()}{job["step"]:03d}.m{index}'
                prior=mechanisms.get(ref)
                if prior is None:
                    mechanisms[ref]=dict(status='pending',original_step=job['step'],original_role=job['role'])
                else:
                    prior['status']='pending';prior.pop('disposition',None)
                    if state.get('extra_grant') and state['extra_grant']['mechanism_ref']==ref:
                        state['extra_grant']=None
                mechanisms[ref].update(label=item['label'],minimal_test=minimal_test_text(item.get('minimal_test',item['label'])),source_step=job['step'],source_role=job['role'])
                pending=next((row for row in remaining if row['mechanism_ref']==ref),None)
                if pending is None:remaining.append(dict(mechanism_ref=ref,planned_step=job['step']))
                else:pending['planned_step']=job['step']
                state['authorized'].add(ref)
            if plan.get('scope') is not None:state['scope']=plan['scope']
            focus=plan.get('focus')
            state['plan']=dict(scope=plan.get('scope'),
                commitments=[dict(label=item['label'],minimal_test=minimal_test_text(item.get('minimal_test',item['label'])),existing_ref=item.get('existing_ref')) for item in plan.get('commitments',[])],
                focus={key:focus[key] for key in ('index','action')} if focus is not None else None)
        remaining[:]=[row for row in remaining if mechanisms[row['mechanism_ref']]['status']!='waived']
        from .scientific_intent import resolve_discriminating_work
        if _sealed_portfolio_applicable(root,job,state):
            state['order']['discriminating_work']=resolve_discriminating_work(state,job,payload)
        state['focus']=copy.deepcopy(receipt['resolved_focus'])
        state['order']['allocation']=copy.deepcopy(receipt['resolved_allocation'])
    else:
        state['focus']=None;state['order']['allocation']=None
    state['order']['diagnostics']=copy.deepcopy(receipt.get('diagnostics',[]))


def fold(root,sources,*,recovery_refs=()):
    """Advance the sealed reducer only for sources beyond its saved cursor."""
    from . import handoff_state
    sources=list(sources)
    steps=tuple(row[0]['step'] for row in sources)
    refs=list(recovery_refs)
    context=handoff_state.current(root)
    key=(steps,json.dumps(refs,sort_keys=True))
    if context is not None and key in context.search:
        return copy.deepcopy(context.search[key])
    initial=None;completed=0
    if context is not None:
        for (prior_steps,prior_refs),value in reversed(list(context.search.items())):
            if len(prior_steps)<len(steps) and steps[:len(prior_steps)]==prior_steps:
                initial=value;completed=prior_steps[-1] if prior_steps else 0
                break
    snapshot=Path(root)/'host-history/search-state.json'
    if initial is None and snapshot.exists():
        saved=c.read(snapshot)
        prior=tuple(saved['steps'])
        if steps[:len(prior)]==prior:
            initial=handoff_state.decode_search(saved['state'])
            completed=prior[-1] if prior else 0
            if prior==steps and saved['recovery_refs']==refs:
                if context is not None:handoff_state.remember_search(context,key,initial)
                return copy.deepcopy(initial)
    with handoff_state.loading():
        state=_fold(root,sources,recovery_refs=refs,initial=initial,completed=completed)
    if context is not None:handoff_state.remember_search(context,key,copy.deepcopy(state))
    # Disposable reducer state stores summaries and identity references only.
    # Sealed results and observations remain the scientific evidence originals.
    return state


def _fold(root,sources,*,recovery_refs=(), initial=None, completed=0):
    from . import execution_recovery
    sources=list(sources)
    def ref_key(ref):
        return (json.dumps(ref.get('origin_identity'),sort_keys=True),ref['path'])
    all_refs={ref_key(ref):ref for ref in recovery_refs};source_refs={}
    for job,_,_,_ in sources:
        owner=originals.node(root,job['step'])
        refs=originals.recovery_refs(owner,job)
        source_refs[job['step']]=refs
        for ref in refs:
            prior=all_refs.setdefault(ref_key(ref),ref)
            if prior!=ref:raise ValueError('Frozen execution recovery prefixes disagree')
    ordered=[all_refs[key] for key in sorted(all_refs)]
    checked=execution_recovery.sealed_values(root,ordered)
    cache={ref_key(ref):value for ref,value in zip(ordered,checked)}
    state=dict(opportunity_counts={},opportunity_grants={},opportunity_unknown=False,extra_grant=None,mechanisms={},current_h=None,scope=None,plan=None,authorized=set(),focus=None,accepted_slices=[],repairs={},
        order=dict(remaining=[],seen_w=set(),allocation=None,diagnostics=[],discriminating_work=[]))
    state['portfolio']=dict(invested={},deferred={},seen_w=set(),pending_w=None,last_branch=None,last_decision_step=None,cycles=[],mainline_reviews=[])
    if initial is not None:
        state=copy.deepcopy(initial)
    original_rows={}
    scientific_sources={job['step']:(job,payload,seal,sha) for job,payload,seal,sha in sources}
    for job,payload,seal,seal_sha in sources:
        if job['step'] <= completed:
            observed=history.read_observation(root,job,seal) or {}
            original_rows.update(execution_recovery.sealed_pending_rows(root,[(job,payload,seal,seal_sha)]))
            for row in observed.get('managed_calls',[]):
                original_rows[(job['step'],row['call_id'])]=dict(row=row,phase='original',native_state=None)
            continue
        # Scientific claims see exactly their own frozen mechanical input prefix.
        _execution_opportunities(root,state,original_rows,source_refs[job['step']],cache)
        if originals.source_settings(originals.node(root,job['step'])).get('search_policy') is not None:apply_source(root,job,payload,seal,state)
        _link_discriminating_responses(job,scientific_sources,state)
        if 'portfolio' in state:_portfolio_update(root,job,payload,seal,state,seal_sha)
        step=job['step'];source=originals.node(root,step)
        intent=originals.source_record(source,'native-intent.json')
        if job['role']=='W' and job.get('search') and intent.exists():
            value=c.read(c.regular(intent))
            if value['task_ref']!=history.source_control_ref(source,f'jobs/{step:03d}.json'):raise ValueError('W dispatch identity changed')
            outcome=c.read(originals.source_record(source,'native-outcome.json'))
            observation=history.read_observation(root,job,seal) if seal.get('observations') else None
            state['accepted_slices'].append(dict(step=step,focus=job['search']['focus'],meter=outcome.get('search_slice'),
                observed_new_candidates=len(observation['candidates']) if observation else None,
                candidate_coverage=observation['coverage']['status'] if observation else 'unknown'))
            focus=job['search']['focus']
            if focus and focus['action']=='repair':
                repair_root=job['search']['repair_root'];state['repairs'][repair_root]=state['repairs'].get(repair_root,0)+1
        observed=history.read_observation(root,job,seal) if seal.get('observations') else {}
        original_rows.update(execution_recovery.sealed_pending_rows(root,[(job,payload,seal,seal_sha)]))
        for row in observed.get('managed_calls',[]):
            original_rows[(job['step'],row['call_id'])]=dict(row=row,phase='original',native_state=None)
    _execution_opportunities(root,state,original_rows,list(recovery_refs),cache)
    return state


def _link_discriminating_responses(job,sources,state):
    """Retain the exact original response without claiming scientific resolution."""
    from . import decision
    for work in state['order'].get('discriminating_work',[]):
        for request in work['executions']:
            step=request['source_a'];row=sources.get(step)
            if row is not None and decision._completed(row[0],row[1],{job['step']:sources[job['step']]}):
                request['response']=dict(source_step=job['step'],role=job['role'],pointer='/scientific_result')


def _execution_opportunities(root,state,original_rows,refs,cache):
    from . import execution_recovery
    state['opportunity_counts']={};state['opportunity_grants']={};state['opportunity_unknown']=False
    for value in execution_recovery.call_view(root,[],refs,original_rows=original_rows,
            validated=[cache[(json.dumps(ref.get('origin_identity'),sort_keys=True),ref['path'])] for ref in refs]).values():
        row=value.get('row')
        if row is None:
            state['opportunity_unknown']=True;continue
        binding=row.get('mechanism_binding')
        if binding is None:continue
        ref=binding['mechanism_ref']
        state['opportunity_counts'][ref]=state['opportunity_counts'].get(ref,0)+1
        grant=binding['grant']
        if grant:
            key=(grant['source_a'],grant['source_a_ref']['path'])
            state['opportunity_grants'][key]=state['opportunity_grants'].get(key,0)+1
        if row['status']=='unknown':state['opportunity_unknown']=True


def project(root,before_step):
    """Explicit full historical audit; normal handoffs use current_state."""
    from . import execution_recovery
    refs=(originals.frozen_recovery_refs(root,before_step) if (Path(root)/originals.MANIFEST).exists()
        else execution_recovery.prefix(root,before_step))
    return fold(root,history.source_sequence(root,before_step),recovery_refs=refs)


def planning_view(state):
    """Separate scheduling progress from retained, authored scientific questions."""
    clip=lambda text:text.encode()[:240].decode('utf-8',errors='ignore')
    deferred=[dict(mechanism_ref=ref,question=clip(item['minimal_test']))
        for ref,item in state['mechanisms'].items() if item['status']=='deferred']
    if state.get('summary') is not None:
        works=[row for name,row in state['summary']['current_facts'].items()
            if name.startswith('search/work/') and row['status']!='withdrawn']
    else:
        works=[row for row in state['order'].get('discriminating_work',[]) if row['status']!='withdrawn']
    return dict(initial_queue=dict(remaining_count=len(state['order']['remaining']),
            processed=not state['order']['remaining']),
        deferred_mechanisms=deferred[:4],deferred_mechanisms_omitted_count=max(0,len(deferred)-4),
        declared_work=[dict(work_ref=row['work_ref'],status=row['status'],question=clip(row['question']))
            for row in works[:4]],declared_work_omitted_count=max(0,len(works)-4),
        scope='Initial queue processing is not scientific resolution. Deferred means temporarily set aside; '
            'retain the declared questions and author limitations. Work responses alone do not resolve them.')



def current_state(root,job,payload=None):
    from . import accepted_search
    refs,works=accepted_search.requested_refs(job,payload)
    state=accepted_search.state(root,job['step'],refs=refs,work_refs=works)
    direct={}
    for source,_,_ in history.direct_sources(root,job) if job.get('direct_refs') else []:
        if source['role']!='W':continue
        fact=state['_node_get'](source['step'])
        if fact is None or fact['role']!='W':raise ValueError('Direct assessment Worker fact is absent')
        direct[source['step']]=fact
        if fact.get('assessed'):
            owner=originals.node(root,source['step'])
            identity=(source['step'],c.record_path(originals.source_record(owner,'result-identity.json')))
            state['order']['seen_w'].add(identity)
    pending=state['portfolio'].get('pending_w')
    workers={}
    if pending and pending['step'] in direct:
        for member in pending.get('steps',[pending['step']]):
            fact=direct.get(member)
            if fact is None:fact=state['_node_get'](member)
            if fact is None or fact['role']!='W':raise ValueError('Pending assessment Worker fact is absent')
            workers[member]=fact
    workers.update(direct)
    state['assessment_workers']=list(workers.values())
    return state


def _current_combination(root,state):
    """Current mechanism declaration, or the exact accepted comparison around its focus."""
    from . import accepted_search
    latest=state['summary'].get('last_science')
    step=latest['step'] if latest and latest['role'] in {'H','W'} else state['current_h'];refs=[]
    focus=state.get('focus')
    if focus and focus['action']!='screen':
        refs=[focus['mechanism_ref']]
        summary=state['summary'];latest=summary['last_science'];request=summary['pending_work_response']
        if latest and latest['role']=='A' and request==dict(source_a=latest['step']):
            facts=summary['current_facts']
            author=facts.get(accepted_search.key('node',latest['step']))
            if author is None:raise ValueError('Current scientific comparison author is missing')
            if author['next_action']=='continue_w':
                for work_ref in author.get('executed_work',[]):
                    work=facts.get(accepted_search.key('work',work_ref))
                    if work is None:raise ValueError('Current scientific comparison work is missing')
                    if (work['status']!='executed' or work['next_action']!='continue_w'
                            or not any(row['source_a']==latest['step'] and row.get('response') is None
                                for row in work['executions'])):continue
                    for ref in work['mechanism_refs']:
                        if ref not in refs:refs.append(ref)
    elif step is not None:
        refs=[ref for ref,item in state['mechanisms'].items() if item['source_step']==step]
    if focus and focus['mechanism_ref'] not in refs:refs.append(focus['mechanism_ref'])
    for ref in refs:
        if ref not in state['mechanisms']:
            item=state['_point_get'](ref)
            if item is None:raise ValueError('Current mechanism original is missing')
            state['mechanisms'][ref]=item;state['authorized'].add(ref)
            state['opportunity_counts'][ref]=item['accepted_calls']
    state['combination_refs']=refs


def _breadth(root,state,limits,focus,*,restricted=False):
    from . import accepted_search, breadth
    if state.get('summary') is None:raise ValueError('Current accepted scientific point state required')
    # Keep declaration order across revisions; never discard authorized pending items.
    refs=list(state.get('combination_refs',[]))
    if not refs and not restricted:return None
    mechanisms=[]
    for ref in refs:
        item=state['mechanisms'][ref];step=item['source_step']
        mechanisms.append(dict(mechanism_ref=ref,label=item['label'],source_step=step,
            source_ref=item['source_ref'],source_role=item['source_role'],
            minimal_test=item['minimal_test'],status=item['status'],ordinal=item['ordinal'],origin_ref=item['origin_ref'],source_goal_id=item['source_origin_identity']['goal_id'],
            **(dict(research_binding=copy.deepcopy(item['research_binding'])) if item.get('research_binding') is not None else {})))
    declared=state.get('extra_grant');grant=None
    if declared and declared['mechanism_ref'] in refs:
        step=declared['source_a'];sha=declared['source_a_ref']
        grant=dict(mechanism_ref=declared['mechanism_ref'],source_a=step,source_a_ref=sha,extra_calls=1,
            used_calls=min(1,declared.get('used_calls',state['opportunity_grants'].get((step,sha['path']),0))))
    value=dict(version=3,initial_calls=limits.get('initial_calls',1),mechanisms=mechanisms,
        baseline=[dict(mechanism_ref=ref,accepted_count=state['opportunity_counts'].get(ref,0)) for ref in refs],
        grant=grant,prior_unknown=state['opportunity_unknown'])
    if restricted:value['restricted_combination']=True
    in_scope=sum(accepted_search._owed(dict(state['mechanisms'][ref],
        accepted_calls=state['opportunity_counts'].get(ref,0)),limits.get('initial_calls',1)) for ref in refs)
    value['outside_initial_owed']=max(0,state['summary']['owed_count']-in_scope)
    value['catalog']=state['_catalog']
    return breadth.validate(value)


def _correction_combination(job,parent_jobs,state):
    """Freeze only identities available to the exact requesting W subject."""
    from . import delivery_review
    if (job.get('research_strategy_version')!=3 or job.get('role')!='W'
            or delivery_review.scope(job)!='delivery_correction'):
        return None
    if parent_jobs is None:raise ValueError('Bounded correction requires its owner-loaded parent jobs')
    requester=parent_jobs.get(job['evidence_return']['step'])
    if requester is None:raise ValueError('Bounded correction requester is missing')
    if requester['role']=='W':
        inherited=(requester.get('search') or {}).get('bounded_correction')
        if not isinstance(inherited,dict):raise ValueError('Bounded correction handoff lost its original combination')
        source_step=inherited['source_step'];source_steps=inherited.get('source_steps',[source_step])
        declared=inherited['mechanism_refs']
    else:
        request=delivery_review.binding(requester)
        if requester['role']!='A' or request is None or request['kind']!='delivery_review':
            raise ValueError('Bounded correction requires its exact pre-delivery assessment')
        source_step=request['step'];source=parent_jobs.get(source_step)
        if source is None:raise ValueError('Bounded correction original subject is missing')
        if source['role']=='W':
            source_steps=[source_step]
        elif (source['role']=='A' and delivery_review.binding(source) is None
                and source.get('purpose') in {None,'assessment'} and not source.get('formal_review')
                and not source.get('final_only') and not source.get('final_report')):
            subjects=[int(Path(ref['path']).parts[2]) for ref in source.get('direct_refs',[])]
            if any(number not in parent_jobs for number in subjects):
                raise ValueError('Bounded correction assessment subject has unloaded direct source facts')
            source_steps=[number for number in subjects if parent_jobs[number]['role']=='W']
            if not source_steps:raise ValueError('Bounded correction assessment has no exact loaded W subject')
        else:raise ValueError('Bounded correction requires its exact W or ordinary assessment subject')
        declared=[]
        for number in source_steps:
            source_search=parent_jobs[number].get('search') or {}
            declared += [row['mechanism_ref'] for row in (source_search.get('breadth') or {}).get('mechanisms',[])
                if row.get('research_binding') is None or row['research_binding']['status']=='active']
            declared += [row['mechanism_ref'] for row in source_search.get('worker_research',{}).get('seed',[])
                if row['status']=='active']
        declared += [ref for ref,item in state['mechanisms'].items() if item['source_step'] in source_steps
            and (item.get('research_binding') is None or item['research_binding']['status']=='active')]
    allowed=set(declared)
    refs=[ref for ref,item in state['mechanisms'].items() if ref in allowed and item['status']!='waived'
        and (item.get('research_binding') is None or item['research_binding']['status']=='active')]
    state['combination_refs']=refs
    return dict(version=1,source_step=source_step,source_steps=source_steps,mechanism_refs=refs)


def bind(root,step,role,protocol=None,*,write_pages=True,state=None,unified=True,job=None,parent_jobs=None):
    settings=configuration_updates.for_new_job(root,step)
    limits=settings.get('search_policy')
    if limits is None:return None
    state=current_state(root,dict(step=step,role=role)) if state is None else state
    if state.get('summary') is None:raise ValueError('Current accepted scientific point state required')
    if role=='W' and state.get('summary') is not None:_current_combination(root,state)
    bounded=_correction_combination(job,parent_jobs,state) if job is not None else None
    focus=state['focus'] if role=='W' else None
    if bounded is not None and focus and focus['mechanism_ref'] not in bounded['mechanism_refs']:
        focus=None
    repair_root=repair_binding=repair_source=repair_authorization=None
    if focus is not None and focus['action']=='repair':
        chooser,_,chooser_seal,_=history._source(root,state['summary']['last_science']['step'])
        chooser_step=chooser['step'];chooser_source=originals.node(root,chooser_step)
        from . import handoff_state
        selected=handoff_state.selected_materials(root,chooser_seal,sources={},source=chooser_source)
        choice=_repair_choice(root,chooser,focus['mechanism_ref'],selected_inputs=selected)
        repair_binding,repair_root,repair_source=choice['status'],choice['root'],choice['source']
        seal=originals.source_record(chooser_source,'result-identity.json')
        repair_authorization=dict(step=chooser_step,job_ref=history.source_control_ref(chooser_source,f'jobs/{chooser_step:03d}.json'),seal_ref=c.record_ref(seal))
    obligation=[]
    if role=='W' and focus and focus['action']=='screen':
        obligation=[dict(mechanism_ref=ref,source_step=item['source_step'],source_ref=item['source_ref'],source_role=item['source_role'])
            for ref,item in state['mechanisms'].items() if ref in state.get('combination_refs',state['authorized']) and item['status']=='pending']
    bound_breadth=_breadth(root,state,limits,focus,restricted=bounded is not None) if role=='W' else None
    advice=None
    if bound_breadth is not None:
        from . import breadth
        decisions,available=breadth._availability(bound_breadth,{},[],head=dict(search=state['summary']),focus=focus)
        advice=dict(available_mechanisms=available[:4],omitted_count=max(0,len(available)-4),
            focus_reason=decisions[focus['mechanism_ref']]['reason'] if focus else None)
    return dict(**(dict(bounded_correction=bounded) if bounded is not None else {}),
        **(dict(worker_research=dict(version=1, exploration=focus is None,
        seed=copy.deepcopy(state.get('worker_research_mechanisms',[]))))
        if role=='W' and settings.get('research_strategy_version')==3 else {}),
        coverage_obligation=obligation,focus=focus,allocation=copy.deepcopy(state['order']['allocation']),unfinished_plan=copy.deepcopy(state['order']['remaining']),
        breadth=bound_breadth,admission_advice=advice,intent_diagnostics=state['order']['diagnostics'],repair_root=repair_root,repair_binding=repair_binding,repair_source=repair_source,repair_authorization=repair_authorization,limits=policy(limits),files=[])


def launch_reason(root,job):
    if job['role']!='W':return None
    if job.get('engineering_repair'):return None
    if job.get('input_selection_status')=='unresolved':return 'implementation_unresolved'
    if c.settings(root, job).get('search_policy') is None:return None
    bound=job['search'];focus=bound['focus'];state=current_state(root,job)
    if bound['intent_diagnostics']:return bound['intent_diagnostics'][0]['code']
    if focus is None:
        from . import delivery_review
        if job.get('research_strategy_version')==3 and delivery_review.scope(job)=='delivery_correction':
            bounded=bound.get('bounded_correction');combination=bound.get('breadth')
            if (isinstance(bounded,dict) and bounded.get('version')==1
                    and combination is not None and combination.get('restricted_combination') is True
                    and bounded.get('mechanism_refs')==[row['mechanism_ref'] for row in combination['mechanisms']]):
                return None
            return 'science_plan_empty'
        bootstrap=bound.get('worker_research')
        if (c.settings(root,job).get('research_strategy_version')==3
                and isinstance(bootstrap,dict) and bootstrap.get('version')==1 and bootstrap.get('exploration') is True
                and set(bootstrap) in ({'version','exploration'},{'version','exploration','seed'})
                and not job.get('formal_review') and not job.get('engineering_repair')
                and not job.get('delivery_review') and not job.get('final_only') and not job.get('final_report')
                and job.get('purpose') not in {'closeout','formal_review','engineering_repair','worker_consultation'}):
            from . import delivery_review
            if delivery_review.scope(job) is not None:return 'science_plan_empty'
            return None
        return 'science_plan_empty'
    if focus['mechanism_ref'] not in state['authorized']:return 'search_focus_outside_h_scope'
    if focus['action']=='repair' and bound['repair_binding']!='available':return 'search_repair_source_'+bound['repair_binding']
    if focus['action']=='repair':
        count=state['_repair_get'](bound['repair_root']) if state.get('summary') is not None else state['repairs'].get(bound['repair_root'],0)
        if count>=bound['limits']['max_requested_repairs']:return 'search_repair_limit'
    return None


def validate_payload(root,job,payload):
    if c.settings(root, job).get('search_policy') is None:return
    diagnostics=assess_payload(root,job,payload)
    if diagnostics:
        row=diagnostics[0]
        raise UnresolvedSearch(row['code'],row['subject'],row.get('message','Scientific intent needs clarification'),expected=row.get('expected'))


def accept(root, job):
    if job['role'] == 'W' and launch_reason(root, job):
        raise ValueError('W dispatch violates its frozen search authorization')
    if job.get('search') is None or job['role'] != 'W':
        return None
    stamp = budget.clock_stamp()
    return dict(task_ref=c.record_ref(root / 'jobs' / f'{job["step"]:03d}.json'),
                boot_id=stamp['boot_id'], advance_seconds=budget.snapshot(root)['advance_seconds'])


def stop_reason(root, job, intent, limits, meter):
    anchor = intent.get('search_slice')
    if anchor is None:
        if job['role'] == 'W' and job.get('search') is not None:
            raise ValueError('Accepted W slice has no immutable meter anchor')
        return None
    if (anchor['task_ref'] != c.record_ref(root / 'jobs' / f'{job["step"]:03d}.json')
            or anchor['boot_id'] != budget.clock_stamp()['boot_id']):
        raise ValueError('Accepted W slice meter identity changed')
    duration = limits['advance_seconds'] - anchor['advance_seconds']
    if duration < 0:
        raise ValueError('W slice consumption cannot move backwards')
    if budget.node_mode(root) == 'soft':
        return None
    if duration >= job['search']['limits']['max_w_seconds']:
        return 'w_slice_time_limit'
    if meter['input_tokens'] + meter['output_tokens'] >= job['search']['limits']['max_w_observed_tokens']:
        return 'w_slice_token_limit'
    return None


def report(root, job, intent, meter):
    anchor = intent.get('search_slice')
    if anchor is None:
        return None
    if anchor['boot_id'] != budget.clock_stamp()['boot_id']:
        raise ValueError('W slice boot changed before final metering')
    duration = budget.snapshot(root)['advance_seconds'] - anchor['advance_seconds']
    if duration < 0:
        raise ValueError('W slice consumption cannot move backwards')
    return dict(advance_seconds=duration, input_tokens=meter['input_tokens'],
                cached_input_tokens=meter['cached_input_tokens'], output_tokens=meter['output_tokens'],
                observed_tokens=meter['input_tokens'] + meter['output_tokens'], dispatches=1)


def portfolio_policy(value):
    if (not isinstance(value, dict) or set(value) not in ({'review_after_cycles'}, {'review_after_cycles', 'version'})
            or type(value['review_after_cycles']) is not int or value['review_after_cycles'] <= 0):
        raise ValueError('Portfolio review needs a positive completed-cycle frequency')
    version = value.get('version', 2)
    if type(version) is not int or version != 2:
        raise ValueError('Unsupported frozen portfolio semantics')
    return dict(review_after_cycles=value['review_after_cycles'],version=2)


def _portfolio_cycle(portfolio, mechanisms, branch, branches=None):
    branches=set([branch] if branches is None else branches)
    for ref in branches:portfolio['invested'][ref]=portfolio['invested'].get(ref,0)+1
    for ref, item in mechanisms.items():
        if ref not in branches and item['status'] != 'waived':
            portfolio['deferred'][ref] = portfolio['deferred'].get(ref, 0) + 1
        else:
            portfolio['deferred'].pop(ref, None)
    portfolio['last_branches']=sorted(branches)
    portfolio['last_branch']=next(iter(branches)) if len(branches)==1 else None


def _portfolio_update(root, job, payload, seal, state, seal_sha=None):
    portfolio, mechanisms = state['portfolio'], state['mechanisms']
    if job['role'] == 'W':
        focus = (job.get('search') or {}).get('focus')
        if focus and focus['mechanism_ref'] in mechanisms:
            identity=(job['step'],seal_sha['path'])
            if focus['action']=='repair':
                original=job['search']['repair_root']
                identity=(original,history.source_seal_ref(originals.node(root,original))['path'])
            branches=[focus['mechanism_ref']]
            if (job.get('search') or {}).get('breadth'):
                observation=history.read_observation(root,job,seal) if seal.get('observations') else {}
                branches=list(dict.fromkeys(row['mechanism_binding']['mechanism_ref'] for row in observation.get('managed_calls',[]) if row.get('mechanism_binding') is not None))
            if focus['action']=='repair':
                for cycle in portfolio.setdefault('cycles',[]):
                    if cycle['w_identity']==identity:cycle['sample_exclusion']='repair_cycle_consumption_incomplete'
            previous=portfolio['pending_w']
            linked=bool(previous and any(ref['path']==f'inputs/parents/{previous["step"]:03d}/conclusion.json' for ref in job.get('direct_refs',[])))
            if linked:
                branches=list(dict.fromkeys([*previous.get('branches',[previous['branch']]),*branches]))
                identity=previous['identity']
            portfolio['pending_w']=dict(step=job['step'],identity=identity,branch=focus['mechanism_ref'],branches=branches,
                steps=[*previous.get('steps',[previous['step']]),job['step']] if linked else [job['step']],
                completed=completed_worker(job,payload),repair=focus['action']=='repair' or bool(linked and previous['repair']))
        return
    if job['role'] != 'A' or job.get('purpose') in {'formal_review','worker_consultation'} or job.get('formal_review'):
        return
    control = seal.get('control_outcome', {})
    valid = all(control.get(key) != 'unresolved' for key in ('search_status', 'selection_status', 'decision_status'))
    ordinary = not job.get('author_correction') and payload.get('next_action') not in {'audit', 'formal_review', 'extract', 'wait'}
    pending = portfolio['pending_w']
    original_cycle = ordinary
    if job.get('author_correction') and pending:
        original = history._source(root, job['author_correction']['root']['step'])[0]
        original_cycle = (original['role'] == 'A' and any(ref['path'] == f'inputs/parents/{pending["step"]:03d}/conclusion.json' for ref in original['direct_refs']))
    focus=state['focus']
    repairing=focus and focus['action']=='repair' and pending and focus['mechanism_ref'] in (pending.get('branches',[pending['branch']]))
    final=bool(job.get('final_report') or job.get('purpose')=='closeout' or job.get('final_only'))
    direct=bool(pending and any(ref['path'] == f'inputs/parents/{pending["step"]:03d}/conclusion.json' for ref in job['direct_refs']))
    if valid and original_cycle and payload.get('next_action') not in {'audit', 'formal_review', 'extract', 'wait'} and pending and pending.get('completed',True) and (direct or final):
        if pending['identity'] not in portfolio['seen_w']:
            _portfolio_cycle(portfolio, mechanisms, pending['branch'],pending.get('branches'))
            portfolio['seen_w'].add(pending['identity'])
            portfolio.setdefault('cycles',[]).append(dict(w_identity=pending['identity'],w_step=pending['step'],w_steps=copy.deepcopy(pending.get('steps',[pending['step']])),a_steps=[job['step']],mechanism_refs=list(pending.get('branches',[pending['branch']])),
                sample_exclusion='repair_cycle_consumption_incomplete' if pending.get('repair') else 'author_correction_cycle_consumption_incomplete' if job.get('author_correction') else None))
        portfolio['pending_w'] = None
    if valid and job.get('author_correction'):
        root_step=job['author_correction']['root']['step']
        for cycle in portfolio.setdefault('cycles',[]):
            if root_step in cycle['a_steps'] and job['step'] not in cycle['a_steps']:
                cycle['a_steps'].append(job['step'])
                cycle['sample_exclusion']='author_correction_cycle_consumption_incomplete'
    # Evidence requests do not manufacture another W+A cycle. The neutral
    # decision source records no claim that the portfolio was adequately reviewed.
    if valid and payload.get('next_action') in {'continue_w','revise_h','finish'} and (payload.get('scientific_result') or payload.get('route_reason')):
        # A's judgment is retained without erasing observed investment.
        portfolio['last_decision_step']=job['step']



def portfolio_view(state,threshold,*,prospective=False,version=2):
    if state.get('summary') is not None:
        summary=state['summary'];actual=summary['portfolio'];count=actual['completed_cycle_count'];pending=actual['pending_w']
        hypothetical=bool(prospective and pending and pending.get('completed',True))
        if hypothetical:
            cycle=state['_node_get'](pending['cycle_root'])
            hypothetical=bool(cycle and not cycle['portfolio_assessed'])
        overdue=actual['overdue_count']
        if hypothetical and threshold is not None:
            next_cycle=count+1
            overdue+=sum(item['status']!='waived' and item.get('due_cycle')==next_cycle for item in state['mechanisms'].values())
            work=state['_node_get'](pending['step'])
            for ref in work.get('branches',[pending['branch']]):
                item=state['mechanisms'].get(ref) or state['_point_get'](ref)
                if item and item['status']!='waived' and item.get('due_cycle') is not None and item['due_cycle']<=next_cycle:overdue-=1
        branches=actual.get('last_branches',[]);branch_count=actual.get('last_branch_count',len(branches))
        return dict(version=2,review_after_cycles=threshold,last_assessed_branch=actual['last_branch'],
            last_assessed_branches=branches,last_assessed_branch_count=branch_count,last_assessed_branches_omitted_count=max(0,branch_count-len(branches)),
            completed_cycles=count,completed_cycle_count=count,cycles_if_assessed=count+int(hypothetical),
            participation=[dict(mechanism_ref=ref,participated_cycles=item.get('participated_cycles',0),participation_fraction=item.get('participated_cycles',0)/count if count else None) for ref,item in state['mechanisms'].items()],
            pending_assessment=bool(pending),pending_w=(dict(source_step=pending['step'],mechanism_refs=pending['branches'],completed=pending.get('completed',True)) if pending else None),
            main_line=actual['main_line'],mainline_streak=actual['mainline_streak'],mainline_source=actual['mainline_source'],
            overdue_count_if_assessed=overdue,review_reminder=False,
            unexcluded_count=actual['unexcluded_count'],last_decision_step=actual['last_decision_step'],
            scope='Counts cover the accepted portfolio. Participation rows are explicitly addressed mechanisms; public navigation retains every original. Counts do not authorize execution or rank scientific merit.')
    actual=state['portfolio'];portfolio=copy.deepcopy(actual)
    pending=actual['pending_w'];count=len(actual['seen_w'])
    hypothetical=bool(prospective and pending and pending.get('completed',True) and pending['identity'] not in actual['seen_w'])
    if hypothetical:_portfolio_cycle(portfolio,state['mechanisms'],pending['branch'],pending.get('branches'))
    overdue=[ref for ref,item in state['mechanisms'].items() if threshold is not None and item['status']!='waived' and portfolio['deferred'].get(ref,0)>=threshold]
    branches=actual.get('last_branches',[])
    reviews=actual.get('mainline_reviews',[]);main=reviews[-1]['main_line'] if reviews else None
    streak=0
    if main is not None:
        for row in reversed(reviews):
            if row['main_line']!=main:break
            streak+=1
    return dict(version=2,review_after_cycles=threshold,last_assessed_branch=actual['last_branch'],
        last_assessed_branches=branches[:4],last_assessed_branch_count=len(branches),last_assessed_branches_omitted_count=max(0,len(branches)-4),
        completed_cycles=count,completed_cycle_count=count,cycles_if_assessed=count+int(hypothetical),
        participation=[dict(mechanism_ref=ref,participated_cycles=actual['invested'].get(ref,0),
            participation_fraction=actual['invested'].get(ref,0)/count if count else None) for ref in state['mechanisms']],
        pending_assessment=bool(pending),pending_w=(dict(source_step=pending['step'],mechanism_refs=list(pending.get('branches',[pending['branch']])),completed=pending.get('completed',True)) if pending else None),
        main_line=main,mainline_streak=streak,mainline_source=(dict(source_step=reviews[-1]['source_step'],pointer='/portfolio_review/main_line') if reviews else None),
        overdue_count_if_assessed=len(overdue),review_reminder=False,
        unexcluded_count=sum(item['status']!='waived' for item in state['mechanisms'].values()),last_decision_step=actual['last_decision_step'],
        scope='Unique independently assessed W cycles; mechanism participation overlaps and may sum above 100%. Pending W and prospective advice are separate. Main-line streak uses authored A judgments, never calls. Counts are not scientific merit, resource costs or execution permission.')


def metrics_view(state,threshold,*,source_step,token_advice=None):
    return dict(portfolio=portfolio_view(state,threshold),
        mechanisms=[dict(mechanism_ref=ref,label=item['label']) for ref,item in state['mechanisms'].items()],
        discriminating_work=copy.deepcopy([row for row in state['order'].get('discriminating_work',[]) if row['status']!='withdrawn']),
        source_step=source_step,partial=False,
        token_advice=token_advice or dict(status='unavailable',reference_cycles=None,minimum_samples=2,
            complete_sample_count=0,excluded_sample_count=0,reasons=['no_current_usage_snapshot'],samples=[],scope='Reported token reference only; no time or admission guarantee.'))
