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


class UnresolvedSearch(ValueError):
    def __init__(self, code, subject, message):
        super().__init__(message)
        self.code, self.subject = code, subject


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


def _object(properties, *, nullable=False):
    return dict(type=['object', 'null'] if nullable else 'object', properties=properties,
                required=list(properties), additionalProperties=False)


def _text(nullable=False):
    return dict(type=['string', 'null'] if nullable else 'string', minLength=1)


def portfolio_review_schema():
    """Authored scientific comparison; nullable only for explicitly exempt turns."""
    text=lambda nullable=False:dict(type=['string','null'] if nullable else 'string',pattern=r'\S')
    evidence=dict(type='array',minItems=1,items=_object(dict(
        source_step=dict(type='integer',minimum=1),pointer=dict(type='string',pattern=r'^/(?:[^~]|~[01])*$'))))
    work=dict(anyOf=[dict(type='null'),_object(dict(existing_ref=dict(type='null'),
        mechanism_refs=dict(type='array',minItems=1,uniqueItems=True,items=text()),question=text(),
        change_criterion=text(),next_action=dict(type='string',enum=['continue_w','revise_h','extract']))),
        _object(dict(existing_ref=text()))])
    update=_object(dict(work_ref=text(),action=dict(type='string',enum=['execute','defer','withdraw']),reason=text(),
        trigger=text(True),revisit=text(True),evidence=dict(type='array',items=evidence['items'])))
    value=_object(dict(main_line=text(True),main_line_reason=text(),
        competitor=_object(dict(mechanism_ref=text(True),explanation=text(),evidence=evidence),nullable=True),
        no_competitor_reason=text(True),discriminating_work=work,no_work_reason=text(True),
        decision=dict(type='string',enum=['continue','switch','reframe','close']),reason=text(),
        deferral=_object(dict(reason=text(),trigger=text(),revisit=text()),nullable=True),
        work_updates=dict(type='array',items=update)),nullable=True)
    value['description']=('For every substantive A assessment of completed W, declare the current main line, strongest viable competing explanation with exact visible scientific source_step/RFC6901 pointer evidence, cheapest feasible discriminating work and continue/switch/reframe/close reason. '
        'Use null with explicit no_competitor_reason/no_work_reason when none is feasible. New explanations route through H. '
        'Every listed active discriminating work needs execute/defer/withdraw; defer needs reason/trigger/revisit, withdraw needs scientific evidence. '
        'continue needs deferral only when a feasible competing work or active work is postponed. Declaration grants no execution; next_action and exact next_focus remain authoritative. '
        'Evidence pointers address fields of original scientific output, e.g. /scientific_result, never host controls, paths or private bindings. Null is only for wait, no completed W, no search, or independent formal review.')
    return value


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
        if prepared is not None and step not in rows:raise ValueError('Prepared direct original is outside its frozen prefix')
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
    if job['role']!='A' or job.get('formal_review') or job.get('purpose')=='formal_review' or c.settings(root,job).get('search_policy') is None:
        return empty
    sources=_frozen_sources(root,job,source_rows=source_rows)
    if state is None:
        from . import execution_recovery
        state=fold(root,[sources[key] for key in sorted(sources)],recovery_refs=originals.recovery_refs(originals.node(root,job['step']),job))
    prepared=None if source_rows is None else {row[0]['step']:row for row in source_rows}
    direct={source['step'] for source,_,_ in _direct_assessment_sources(root,job,prepared)}
    final=bool(job.get('final_report') or job.get('purpose')=='closeout' or job.get('final_only'))
    eligible=[row for step,row in sources.items() if row[0]['role']=='W' and row[1].get('stage_intent')=='review'
        and (step in direct or final)]
    # A final summary without a direct W can assess only the still-pending
    # original inside its own cut. Already assessed history requires coverage,
    # not another invented portfolio judgment.
    if final and not any(row[0]['step'] in direct for row in eligible):
        pending=(state.get('portfolio') or {}).get('pending_w')
        eligible=[row for row in eligible if pending is not None and row[0]['step']==pending['step']]
    result=dict(applicable=bool(eligible and state['mechanisms']),mechanisms=copy.deepcopy(state['mechanisms']),
        completed_w=[dict(step=row[0]['step'],limitations=copy.deepcopy(row[1].get('limitations',[])),
            delivery=copy.deepcopy(row[2].get('control_outcome',{}).get('worker_delivery'))) for row in eligible],
        discriminating_work=copy.deepcopy(state['order'].get('discriminating_work',[])),sources=sources,final=final)
    frozen=job.get('portfolio_context')
    if source_rows is None and frozen is not None and (
            frozen['required_for_completed_w']!=result['applicable']
            or frozen['assessed_w']!=[dict(source_step=row['step'],pointer='/scientific_result') for row in result['completed_w']]
            or frozen['active_work_count']!=sum(row['status']!='withdrawn' for row in result['discriminating_work'])):
        raise ValueError('Portfolio context differs from its frozen scientific originals')
    return result


def validate_science_refs(root,job,refs):
    """Check identity and author-visible fields only, never scientific merit."""
    sources=_frozen_sources(root,job)
    for ref in refs:
        if not isinstance(ref,dict) or set(ref)!={'source_step','pointer'} or type(ref['source_step']) is not int:
            raise ValueError('Scientific evidence needs source_step and RFC6901 pointer')
        row=sources.get(ref['source_step']);pointer=ref['pointer']
        if row is None or not isinstance(pointer,str) or not pointer.startswith('/') or re.search(r'~(?![01])',pointer):
            raise ValueError('Scientific evidence is outside the frozen visible prefix')
        source,payload,_,_=row
        keys=[key.replace('~1','/').replace('~0','~') for key in pointer[1:].split('/')]
        fields=c.result_schema(source['role'],source['handoff_protocol'],'selection' in source,**c.response_options(source))['properties']
        if keys[0] not in fields or keys[0] in {'implementation_selection'}:
            raise ValueError('Scientific evidence points outside declared scientific fields')
        value=payload
        for key in keys:
            if isinstance(value,dict) and key in value:value=value[key]
            elif isinstance(value,list) and re.fullmatch(r'0|[1-9][0-9]*',key) and int(key)<len(value):value=value[int(key)]
            else:raise ValueError('Scientific evidence pointer does not resolve')
    return copy.deepcopy(refs)


def require_portfolio_review(root,job,payload,*,context=None,interpretation=None):
    """Raw pre-seal completeness gate, also used by immutable replay."""
    from .output_repair import OutputRepairRequired
    import jsonschema
    if job['role']!='A' or payload.get('next_action')=='wait':return None
    context=assessment_context(root,job) if context is None else context
    if not context['applicable']:return None
    def reject(detail):
        raise OutputRepairRequired('portfolio_review_incomplete',
            'Correct portfolio_review in this same A output: '+detail+'. Retain completed originals; do not repeat experiments.')
    review=payload.get('portfolio_review')
    if review is None or not c.json_field_representable('portfolio_review',review) or not jsonschema.Draft202012Validator(portfolio_review_schema()).is_valid(review):
        reject('supply a complete nonblank authored comparison')
    mechanisms=context['mechanisms'];main=review['main_line'];action=payload.get('next_action')
    if main is not None and main not in mechanisms:reject('main_line must be an exact listed mechanism')
    competitor=review['competitor'];work=review['discriminating_work']
    if (competitor is None)!=(review['no_competitor_reason'] is not None) or (work is None)!=(review['no_work_reason'] is not None):
        reject('pair absent competitor/work with its explicit reason only')
    if competitor is not None and competitor['mechanism_ref'] is not None and (competitor['mechanism_ref'] not in mechanisms or competitor['mechanism_ref']==main):
        reject('competitor must name a different exact mechanism or a new explanation')
    refs=[] if competitor is None else list(competitor['evidence'])
    active={row['work_ref']:row for row in context['discriminating_work'] if row['status']!='withdrawn'}
    updates=review['work_updates'];names=[row['work_ref'] for row in updates]
    if len(names)!=len(set(names)) or set(names)!=set(active):reject('update every active work_ref exactly once, with no unknown refs')
    for update in updates:
        refs.extend(update['evidence'])
        if update['action']=='defer' and (update['trigger'] is None or update['revisit'] is None):reject('deferred work needs trigger and revisit')
        if update['action']=='withdraw' and not update['evidence']:reject('withdrawal needs exact scientific evidence')
        if update['action']=='execute' and (work is None or work['existing_ref']!=update['work_ref']):reject('executed work must be the selected discriminating work')
    if work is not None:
        if work['existing_ref'] is not None:
            if work['existing_ref'] not in active:reject('existing_ref must name active declared work')
            work=dict(active[work['existing_ref']],existing_ref=work['existing_ref'])
        if any(ref not in mechanisms for ref in work['mechanism_refs']):reject('work mechanisms must be exact listed refs')
        if work['next_action']==action:
            if context.get('final'):reject('final assessment cannot dispatch scientific work')
            if work['existing_ref'] is not None and next(row for row in updates if row['work_ref']==work['existing_ref'])['action']!='execute':reject('selected dispatched work needs execute')
            request=c.scientific_field(job,payload,'decision_request')
            if request is not None and (request['question'].strip()!=work['question'].strip() or request['change_criterion'].strip()!=work['change_criterion'].strip()):reject('executed work must match the actual decision_request')
        elif any(row['action']=='execute' for row in updates):reject('execute must match actual next_action')
    if review['decision']=='close' and action not in {'finish','deliver'}:reject('close requires finish or deliver')
    if action in {'finish','deliver'} and review['decision']!='close':reject('ending requires a close judgment')
    if review['decision']=='reframe' and action!='revise_h':reject('reframe requires revise_h')
    if action=='continue_w':
        focus=(interpretation or {}).get('focus')
        if focus is None or main is None or focus['mechanism_ref']!=main:reject('continue_w needs resolved next_focus matching main_line')
        if work is not None and work['next_action']==action and main not in work['mechanism_refs']:reject('executed work must cover the resolved mechanism focus')
    deferred=work is not None and work['existing_ref'] is None and work['next_action']!=action
    if review['decision']=='continue' and deferred and review['deferral'] is None:reject('postponed feasible work needs reason, trigger and revisit')
    try:validate_science_refs(root,job,refs)
    except ValueError:reject('scientific evidence must resolve inside exact declared visible originals')
    return review


def schema(role, protocol=None):
    focus=lambda key:_object({key:_text(nullable=True),'action':dict(type='string',enum=list(MODES))},nullable=True)
    if role=='H':
        test=_text();test['description']='State the discriminating scientific prediction or question and why this mechanism has this exploration priority. Do not prescribe run counts, parameter grids or costs.'
        commitment=_object(dict(label=_text(),minimal_test=test,existing_ref=_text(nullable=True)))
        value=_object(dict(scope=_text(),commitments=dict(type='array',items=commitment),focus=focus('label')),nullable=True)
        value['description']=('Declare only additions or changed commitments; omissions preserve existing commitments and dispositions. '
            'Choose action explicitly for a W task: screen covers the confirmed combination; improve, repair and validate are local. Repair uses an exact direct W observation, or accepted input bytes selected through implementation_selection; select one source when ambiguous. A revised existing_ref preserves the old observation identity without inheriting screening. A null label with screen delegates primary selection; a null whole focus cannot dispatch W. Exact ref or unique label with route_reason selects the action.')
        return value
    if role=='A':
        disposition=_object(dict(mechanism_ref=_text(),status=dict(type='string',enum=['screened','deferred','waived','pending']),reason=_text()))
        value=_object(dict(dispositions=dict(type='array',items=disposition),next_focus=focus('mechanism_ref')),nullable=True)
        value['description']=('Declare only changed scientific dispositions; screened requires this mechanism and H version in one exact final W with sealed accepted and known terminal originals; execution success alone is not scientific screening. '
            'Choose the W task action explicitly: screen covers the unfinished combination; improve, repair and validate require one exact mechanism. Null whole next_focus cannot dispatch W; a null mechanism_ref is allowed only with screen. '
            'An explicit exact ref or unique label plus route_reason may deepen another authorized mechanism. Within unchanged H scope, continue_w requests the next finite stage after review; prior usage stays consumed. Do not revise H to reset budgets. Do not write mechanical IDs, counts, paths or priorities.')
        return value
    raise ValueError('Only H and A declare scientific search choices')




def _managed_refs(root,source,seal,state):
    if not seal.get('observations'):return set()
    observation=history.read_observation(root,source,seal);hashes={};refs=set()
    for row in observation.get('managed_calls',[]):
        binding=row.get('mechanism_binding')
        if binding is None:continue
        ref=binding['mechanism_ref']
        current=state['mechanisms'].get(ref)
        if current is None or row['status'] not in {'known_success','known_failed'} or binding['source_h']!=current['source_h']:continue
        step=current['source_h']
        if step not in hashes:hashes[step]=history.source_seal_ref(originals.node(root,step))
        if binding['source_h_ref']==hashes[step]:refs.add(ref)
    return refs


def _screen_refs(root,source,seal,state):
    """A judges this final W's findings using its frozen, reusable observations.

    Reused proofs qualify scientific assessment only. They remain absent from
    this W's managed_calls and its new execution or portfolio investment counts.
    """
    from . import worker_delivery
    if not worker_delivery.applicable(source):return _managed_refs(root,source,seal,state)
    owner=originals.node(root,source['step'])
    if owner.inherited:
        fact_path=originals.source_record(owner,worker_delivery.FACT_NAME)
        native_record=owner.native_record or f'native-evidence/{owner.step:03d}'
        fact_relative=native_record+'/'+worker_delivery.FACT_NAME
        facts=c.read(fact_path)
        intent=c.read(originals.source_record(owner,'native-intent.json'))
        # The seal retains the full original fingerprint; delivery facts bind
        # the selected attempt's ordinary file locator, without that fingerprint.
        raw=originals.resolve_ref(owner,seal['raw_ref'])
        raw_ref=dict(path=str(Path(owner.original_root)/native_record/'science-output.json'),
            size_bytes=raw.stat().st_size)
        if (seal['raw_ref']['path']!=raw_ref['path'] or facts.get('version')!=1
                or facts.get('binding')!=dict(job_ref=seal['job_ref'],
                    raw_ref=raw_ref,source_attempt=intent.get('attempt',0))):
            raise history.ObserverIntegrityError('Inherited W evidence changed its exact attempt')
        from . import execution_recovery
        recovered=execution_recovery.resolved(root,originals.recovery_refs(owner,source))
        for row in facts['evidence']:
            accepted=worker_delivery.accepted_original(root,row['accepted'],source=owner)
            producer=(originals.at_step(originals.source_for_ref(owner,row['accepted']),accepted['scope']['step'])
                if 'origin_identity' in row['accepted'] else originals.dependency(owner,accepted['scope']['step']))
            producer_job=originals.source_job(producer)
            producer_seal=c.read(originals.source_record(producer,'result-identity.json'))
            observed=c.read(originals.resolve_ref(producer,producer_seal['observations']))
            calls=[recovered.get((producer.step,call['call_id']),call) for call in observed.get('managed_calls',[])]
            proofs={}
            for key in ('accepted','completed','terminal','observation'):
                proof=row.get(key)
                if proof is not None and proof.get('origin_identity',producer.origin_identity)!=producer.origin_identity:
                    raise history.ObserverIntegrityError('Inherited W evidence changed its exact observation origin')
                proofs[key]=({field:value for field,value in proof.items() if field!='origin_identity'}
                    if proof is not None else None)
            matches=[call for call in calls if all(call.get(key)==proofs[key] for key in proofs)]
            if len(matches)!=1 or matches[0]['status'] not in {'known_success','known_failed'}:
                raise history.ObserverIntegrityError('Inherited W evidence lacks its exact known observation')
            history.validate_managed_original(producer,producer_job,matches)
    else:
        record=execution.record(root,source['step'])
        facts=worker_delivery.validate(root,source,record)
        fact_path=c.regular(record/worker_delivery.FACT_NAME)
        fact_relative=fact_path.relative_to(owner.storage_root).as_posix()
    receipt=seal['control_outcome'].get('worker_delivery') or {}
    if receipt.get('facts_ref')!=history.source_control_ref(owner,fact_relative):
        raise history.ObserverIntegrityError('Final W scientific evidence differs from its seal')
    science=c.science_payload(originals.source_raw(owner),'W',source['handoff_protocol'])
    declared={};mechanisms=source['search']['breadth']['mechanisms']
    for finding in c.scientific_field(source,science,'mechanism_findings') or []:
        matches=[item['mechanism_ref'] for item in mechanisms
            if finding['mechanism'] in (item['mechanism_ref'],item['label'])]
        if len(matches)==1:declared.setdefault(matches[0],[]).append(finding)
    refs=set();hashes={}
    for row in facts['evidence']:
        ref=row['mechanism_ref'];findings=declared.get(ref,[]);current=state['mechanisms'].get(ref)
        if current is None or len(findings)!=1 or findings[0]['basis']!='observed' or not findings[0]['finding'].strip():continue
        binding=worker_delivery.accepted_original(root,row['accepted'],source=owner)['mechanism_binding'];step=current['source_h']
        if step not in hashes:hashes[step]=history.source_seal_ref(originals.node(root,step))
        if binding['source_h']==step and binding['source_h_ref']==hashes[step]:refs.add(ref)
    return refs


def _mechanism_sources(root,job,ref,state,*,screen=False):
    sources=[]
    for source,_,seal in history.direct_sources(root,job):
        if source['role']!='W':continue
        if (source.get('search') or {}).get('breadth'):
            refs=_screen_refs(root,source,seal,state) if screen else _managed_refs(root,source,seal,state)
            if ref in refs:sources.append(source)
        elif ((source.get('search') or {}).get('focus') or {}).get('mechanism_ref')==ref and (not screen or source['search']['focus']['action']=='screen'):
            sources.append(source)
    return sources



def _repair_subject(root,job,ref,*,payload=None):
    """Bind one old known observation; current H authority is a separate identity.

    Direct W inputs are eligible. An existing exact implementation selection may
    name an older W and disambiguate its accepted input bytes, never its outputs.
    No labels, chronology or current-H qualification select an observation.
    """
    direct={source['step']:(source,seal) for source,_,seal in history.direct_sources(root,job) if source['role']=='W'}
    selected=c.selected_inputs(c.workspace(root,job['step']),payload or {},job)
    sources=dict(direct);inputs={}
    for item in selected:
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
    source,seal,row=known[0];binding=row['mechanism_binding'];old_h=binding['source_h']
    # Validate the old observation under its OWN H seal, even after a revision.
    original_h,_,_,sha=history._source(root,old_h)
    if original_h['role']!='H' or binding['source_h_ref']!=sha:
        return dict(status='version_mismatch',root=None,source=None)
    identity=originals.source_record(originals.node(root,source['step']),'result-identity.json')
    subject=dict(step=source['step'],job_ref=seal['job_ref'],raw_ref=seal['raw_ref'],
        seal_ref=c.record_ref(identity),call=copy.deepcopy(row))
    return dict(status='available',root=source['search'].get('repair_root') or source['step'],source=subject)


def _repair_choice(root,job,ref,*,payload=None):
    try:return _repair_subject(root,job,ref,payload=payload)
    except FileNotFoundError:
        return dict(status='missing_original',root=None,source=None)
    except ValueError as error:
        if str(error)!='Expected an original regular file':raise
        return dict(status='missing_original',root=None,source=None)


def _repair_diagnostic(root,state,choice,job):
    if choice['status']!='available':return 'search_repair_source_'+choice['status']
    if state['repairs'].get(choice['root'],0)>=c.settings(root,job)['search_policy']['max_requested_repairs']:
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


def _apply_science(root,job,payload,state,diagnostics):
    mechanisms=state['mechanisms']
    if job['role']=='H':
        state['current_h']=job['step']
        plan=c.scientific_field(job,payload,'search_plan')
        if plan is not None:
            if not plan['scope'].strip():raise UnresolvedSearch('h_scope_empty','scope','H scope needs scientific content')
            labels=set();refs=set()
            for index,item in enumerate(plan['commitments'],1):
                if not item['label'].strip() or not item['minimal_test'].strip() or item['label'] in labels:
                    raise UnresolvedSearch('h_mechanism_unresolved',item['label'],'H commitments need distinct labels and minimal tests')
                labels.add(item['label']);ref=item['existing_ref']
                if ref is not None and ref not in mechanisms:raise UnresolvedSearch('h_mechanism_unresolved',ref,'Revision requires an exact registered mechanism')
                ref=ref or f'h{job["step"]:03d}.m{index}'
                if ref in refs:raise UnresolvedSearch('h_mechanism_unresolved',ref,'A revision cannot repeat one mechanism')
                refs.add(ref)
                if ref not in mechanisms:
                    mechanisms[ref]=dict(status='pending',original_h=job['step'])
                    state['order']['remaining'].append(dict(mechanism_ref=ref,planned_step=job['step']))
                else:
                    mechanisms[ref]['status']='pending'
                    mechanisms[ref].pop('disposition',None)
                    remaining=state['order']['remaining']
                    match=next((row for row in remaining if row['mechanism_ref']==ref),None)
                    if match is None:remaining.append(dict(mechanism_ref=ref,planned_step=job['step']))
                    else:match['planned_step']=job['step']
                    if state.get('extra_grant',{} ) and state['extra_grant']['mechanism_ref']==ref:state['extra_grant']=None
                mechanisms[ref].update(label=item['label'],minimal_test=item['minimal_test'],source_h=job['step'])
            state['scope']=plan['scope'];state['plan']=plan;state['authorized']|=refs
    elif job['role']=='A':
        review=c.scientific_field(job,payload,'search_review') or dict(dispositions=[])
        repeated={item['mechanism_ref'] for item in review['dispositions'] if sum(other['mechanism_ref']==item['mechanism_ref'] for other in review['dispositions'])>1}
        for item in review['dispositions']:
            ref=item['mechanism_ref']
            if ref not in mechanisms or ref in repeated:
                diagnostics.append(dict(code='a_mechanism_unresolved',subject=ref));continue
            if not item['reason'].strip():
                diagnostics.append(dict(code='a_disposition_reason_required',subject=ref));continue
            if item['status']==mechanisms[ref]['status']:continue
            if item['status']=='screened':
                screens=_mechanism_sources(root,job,ref,state,screen=True)
                if len(screens)!=1:
                    diagnostics.append(dict(code='screen_source_ambiguous' if screens else 'screen_source_unavailable',subject=ref));continue
            mechanisms[ref]['status']=item['status']
            mechanisms[ref]['disposition']=dict(reason=item['reason'],source_a=job['step'])
            if item['status']=='pending' and not any(row['mechanism_ref']==ref for row in state['order']['remaining']):
                state['order']['remaining'].append(dict(mechanism_ref=ref,planned_step=job['step']))
    from .scientific_intent import resolve_intent
    intent=dict(payload)
    for field in ('search_plan','search_review'):intent[field]=c.scientific_field(job,payload,field)
    intent['portfolio_review']=c.scientific_field(job,payload,'portfolio_review') if job['role']=='A' else None
    if intent['portfolio_review'] is not None and not assessment_context(root,job,state=state)['applicable']:
        intent['portfolio_review']=None
    interpretation=resolve_intent(state,job,intent,assessed=_assessed(root,job,state))
    diagnostics.extend(interpretation['diagnostics'])
    focus=interpretation['focus']
    if job['role'] in {'H','A'} and payload.get('next_action') in {'experiment','continue_w'} and focus and focus['action']=='repair' and not diagnostics:
        choice=_repair_choice(root,job,focus['mechanism_ref'],payload=payload)
        code=_repair_diagnostic(root,state,choice,job)
        if code:diagnostics.append(dict(code=code,subject=focus['mechanism_ref']))
    state['focus']=focus
    if job['role']=='A' and interpretation['focus'] and ((c.scientific_field(job,payload,'search_review') or {}).get('next_focus') or {}).get('mechanism_ref') is not None and not diagnostics:
        state['extra_grant']=dict(mechanism_ref=interpretation['focus']['mechanism_ref'],source_a=job['step'])
    state['order'].update(remaining=interpretation['unfinished_plan'],seen_w=interpretation['seen_w'],allocation=interpretation['allocation'],diagnostics=diagnostics,
        discriminating_work=interpretation['discriminating_work'])


def interpret(root,job,payload,*,state=None):
    """One prospective interpretation; callers do not publish its mutations."""
    state=copy.deepcopy(project(root,job['step']) if state is None else state)
    if job.get('purpose')=='formal_review' or job.get('formal_review') or job.get('engineering_repair'):
        return dict(state=state,focus=None,allocation=None,diagnostics=[])
    if job.get('purpose')=='closeout' or job.get('final_report'):
        from .scientific_intent import resolve_discriminating_work
        review=c.scientific_field(job,payload,'portfolio_review')
        if review is not None and assessment_context(root,job,state=state)['applicable']:
            state['order']['discriminating_work']=resolve_discriminating_work(state,job,dict(payload,portfolio_review=review))
        return dict(state=state,focus=None,allocation=None,diagnostics=[])
    diagnostics=[]
    field='search_plan' if job['role']=='H' else 'search_review' if job['role']=='A' else None
    if field is not None and payload.get(field) is not None and c.scientific_field(job,payload,field) is None:
        diagnostics.extend(row for row in c.scientific_diagnostics(job,payload) if row['subject']==field)
    try:_apply_science(root,job,payload,state,diagnostics)
    except UnresolvedSearch as error:diagnostics.append(dict(code=error.code,subject=error.subject))
    return dict(state=state,focus=state['focus'],allocation=state['order']['allocation'],diagnostics=diagnostics)


def assess_payload(root,job,payload):
    return interpret(root,job,payload)['diagnostics']


def apply_source(root,job,payload,seal,state):
    from .protocol import CURRENT
    receipt=seal['control_outcome']
    if job.get('handoff_protocol')!=CURRENT or receipt.get('binding_protocol')!=CURRENT:
        raise ValueError('Control interpretation binding changed')
    result=interpret(root,job,payload,state=state)
    diagnostics=result['diagnostics']
    reported=[row for row in receipt['diagnostics'] if not row['code'].startswith(
        ('implementation_','a_decision_','candidate_recommendation_'))
        and (row['code']!='scientific_field_unresolved' or row['subject'] in {'search_plan','search_review'})]
    if (receipt['search_status']!=('unresolved' if diagnostics else 'applied') or reported!=diagnostics[:len(reported)]
            or len(reported)<len(diagnostics) and not receipt['omitted_count']
            or receipt['resolved_focus']!=result['focus'] or receipt['resolved_allocation']!=result['allocation']):
        raise ValueError('Scientific intent differs from its sealed interpretation')
    if job.get('purpose')=='formal_review' or job.get('formal_review') or job.get('engineering_repair'):return
    if all(receipt.get(key)!='unresolved' for key in ('search_status','selection_status','decision_status')):
        state.clear();state.update(result['state'])
    else:
        state['focus']=None
        state['order']['allocation']=None
        state['order']['diagnostics']=receipt['diagnostics']


def fold(root,sources,*,recovery_refs=()):
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
    checked=execution_recovery.validate(root,ordered)
    cache={ref_key(ref):value for ref,value in zip(ordered,checked)}
    state=dict(opportunity_counts={},opportunity_grants={},opportunity_unknown=False,extra_grant=None,mechanisms={},current_h=None,scope=None,plan=None,authorized=set(),focus=None,accepted_slices=[],repairs={},
        order=dict(remaining=[],seen_w=set(),allocation=None,diagnostics=[],discriminating_work=[]))
    state['portfolio']=dict(invested={},deferred={},seen_w=set(),pending_w=None,last_branch=None,last_decision_step=None,cycles=[],mainline_reviews=[])
    original_rows={}
    scientific_sources={job['step']:(job,payload,seal,sha) for job,payload,seal,sha in sources}
    for job,payload,seal,seal_sha in sources:
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
    """An observed response is traceable execution, never scientific resolution."""
    from . import decision
    for work in state['order'].get('discriminating_work',[]):
        for request in work['executions']:
            step=request['source_a'];row=sources.get(step)
            if row is not None and job['step']==step+1 and decision._completed(row[0],row[1],sources):
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
    from . import execution_recovery
    refs=(originals.frozen_recovery_refs(root,before_step) if (Path(root)/originals.MANIFEST).exists()
        else execution_recovery.prefix(root,before_step))
    return fold(root,history.source_sequence(root,before_step),recovery_refs=refs)



def _breadth(root,state,limits,focus):
    from . import breadth
    # Keep H declaration order across revisions; never discard authorized pending items.
    refs=[item['mechanism_ref'] for item in state['order']['remaining'] if item['mechanism_ref'] in state['authorized']]
    refs+= [ref for ref in state['mechanisms'] if ref in state['authorized'] and ref not in refs]
    if focus and focus['action']!='screen':refs=[focus['mechanism_ref']]
    if not refs:return None
    mechanisms=[]
    for ref in refs:
        item=state['mechanisms'][ref];step=item['source_h']
        mechanisms.append(dict(mechanism_ref=ref,label=item['label'],source_h=step,
            source_h_ref=history.source_seal_ref(originals.node(root,step)),
            minimal_test=item['minimal_test'],status=item['status']))
    declared=state.get('extra_grant');grant=None
    if declared and declared['mechanism_ref'] in refs:
        step=declared['source_a'];sha=history.source_seal_ref(originals.node(root,step))
        grant=dict(declared,source_a_ref=sha,extra_calls=1,used_calls=min(1,state['opportunity_grants'].get((step,sha['path']),0)))
    return breadth.validate(dict(version=1,initial_calls=limits.get('initial_calls',1),mechanisms=mechanisms,
        baseline=[dict(mechanism_ref=ref,accepted_count=state['opportunity_counts'].get(ref,0)) for ref in refs],
        grant=grant,prior_unknown=state['opportunity_unknown']))


def bind(root,step,role,protocol=None,*,write_pages=True,state=None,unified=True):
    limits=configuration_updates.for_new_job(root,step).get('search_policy')
    if limits is None:return None
    state=project(root,step) if state is None else state
    focus=state['focus'] if role=='W' else None
    repair_root=repair_binding=repair_source=repair_authorization=None
    if focus is not None and focus['action']=='repair':
        chooser,payload,_,_=next(source for source in reversed(history.source_sequence(root,step)) if source[0]['role'] in {'H','A'})
        chooser_step=chooser['step'];chooser_source=originals.node(root,chooser_step)
        choice=_repair_choice(root,chooser,focus['mechanism_ref'],payload=payload)
        repair_binding,repair_root,repair_source=choice['status'],choice['root'],choice['source']
        seal=originals.source_record(chooser_source,'result-identity.json')
        repair_authorization=dict(step=chooser_step,job_ref=history.source_control_ref(chooser_source,f'jobs/{chooser_step:03d}.json'),seal_ref=c.record_ref(seal))
    obligation=[]
    if role=='W' and focus and focus['action']=='screen':
        obligation=[dict(mechanism_ref=ref,source_h=item['source_h'],source_h_ref=history.source_seal_ref(originals.node(root,item['source_h'])))
            for ref,item in state['mechanisms'].items() if ref in state['authorized'] and item['status']=='pending']
    return dict(coverage_obligation=obligation,focus=focus,allocation=copy.deepcopy(state['order']['allocation']),unfinished_plan=copy.deepcopy(state['order']['remaining']),
        breadth=_breadth(root,state,limits,focus) if role=='W' else None,intent_diagnostics=state['order']['diagnostics'],repair_root=repair_root,repair_binding=repair_binding,repair_source=repair_source,repair_authorization=repair_authorization,limits=policy(limits),files=[])


def launch_reason(root,job):
    if job['role']!='W':return None
    if job.get('engineering_repair'):return None
    if job.get('input_selection_status')=='unresolved':return 'implementation_unresolved'
    if c.settings(root, job).get('search_policy') is None:return None
    bound=job['search'];focus=bound['focus'];state=project(root,job['step'])
    if bound['intent_diagnostics']:return bound['intent_diagnostics'][0]['code']
    if focus is None:return 'science_plan_empty'
    if focus['mechanism_ref'] not in state['authorized']:return 'search_focus_outside_h_scope'
    if focus['action']=='repair' and bound['repair_binding']!='available':return 'search_repair_source_'+bound['repair_binding']
    if focus['action']=='repair' and state['repairs'].get(bound['repair_root'],0)>=bound['limits']['max_requested_repairs']:return 'search_repair_limit'
    return None


def validate_payload(root,job,payload):
    if c.settings(root, job).get('search_policy') is None:return
    diagnostics=assess_payload(root,job,payload)
    if diagnostics:raise UnresolvedSearch(diagnostics[0]['code'],diagnostics[0]['subject'],'Scientific intent needs clarification')


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
            portfolio['pending_w']=dict(step=job['step'],identity=identity,branch=focus['mechanism_ref'],branches=branches,completed=payload.get('stage_intent')=='review',repair=focus['action']=='repair')
        return
    if job['role'] != 'A' or job.get('purpose')=='formal_review' or job.get('formal_review'):
        return
    control = seal.get('control_outcome', {})
    valid = all(control.get(key) != 'unresolved' for key in ('search_status', 'selection_status', 'decision_status'))
    review=c.scientific_field(job,payload,'portfolio_review')
    if valid and review is not None and payload.get('next_action')!='wait' and (job.get('portfolio_context') or {}).get('required_for_completed_w',True):
        root_step=(job.get('author_correction') or {}).get('root',{}).get('step',job['step'])
        judgments=portfolio.setdefault('mainline_reviews',[])
        row=dict(root_step=root_step,source_step=job['step'],main_line=review['main_line'])
        prior=next((index for index,item in enumerate(judgments) if item['root_step']==root_step),None)
        if prior is None:judgments.append(row)
        else:judgments[prior]=row
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
    if valid and original_cycle and not repairing and payload.get('next_action') not in {'audit', 'formal_review', 'extract', 'wait'} and pending and pending.get('completed',True) and (direct or final):
        if pending['identity'] not in portfolio['seen_w']:
            _portfolio_cycle(portfolio, mechanisms, pending['branch'],pending.get('branches'))
            portfolio['seen_w'].add(pending['identity'])
            portfolio.setdefault('cycles',[]).append(dict(w_identity=pending['identity'],w_step=pending['step'],a_steps=[job['step']],mechanism_refs=list(pending.get('branches',[pending['branch']])),
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
        overdue_count_if_assessed=len(overdue),review_reminder=bool(overdue),
        unexcluded_count=sum(item['status']!='waived' for item in state['mechanisms'].values()),last_decision_step=actual['last_decision_step'],
        scope='Unique independently assessed W cycles; mechanism participation overlaps and may sum above 100%. Pending W and prospective advice are separate. Main-line streak uses authored A judgments, never calls. Counts are not scientific merit, resource costs or execution permission.')


def metrics_view(state,threshold,*,source_step,token_advice=None):
    return dict(portfolio=portfolio_view(state,threshold),
        mechanisms=[dict(mechanism_ref=ref,label=item['label']) for ref,item in state['mechanisms'].items()],
        discriminating_work=copy.deepcopy([row for row in state['order'].get('discriminating_work',[]) if row['status']!='withdrawn']),
        source_step=source_step,partial=False,
        token_advice=token_advice or dict(status='unavailable',reference_cycles=None,minimum_samples=2,
            complete_sample_count=0,excluded_sample_count=0,reasons=['no_current_usage_snapshot'],samples=[],scope='Reported token reference only; no time or admission guarantee.'))
