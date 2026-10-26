"""Authored W comparison previews over the complete retained candidate pool.

Only identity, declaration coverage and counts are checked here. Mechanism
membership, suitability and reasons remain scientific judgments for W and A.
"""
from __future__ import annotations

import copy

from . import contracts as c
from .output_repair import OutputRepairRequired


SCOPE_GUIDANCE = (
    "Use only this job's candidate_nomination_mechanisms: cover each unwaived entry with one representative "
    "or explained gap, including deferred and screened. Screen covers its full list; improve/repair/validate "
    "keep other mechanisms' prior declarations and gaps.")


def configuration(extra_slots):
    if type(extra_slots) is not int or extra_slots < 0:
        raise ValueError('Candidate extra slots must be a nonnegative integer')
    return dict(version=1, extra_slots=extra_slots)


def schema():
    text=dict(type='string',pattern=r'\S')
    original=dict(type='object',properties=dict(call_id=text,artifact=text),
        required=['call_id','artifact'],additionalProperties=False)
    candidate=dict(anyOf=[text,original])
    def rows(properties):
        return dict(type='array',items=dict(type='object',properties=properties,
            required=list(properties),additionalProperties=False))
    fields=dict(representatives=rows(dict(mechanism_ref=text,candidate=candidate,reason=text)),
        extras=rows(dict(candidate=candidate,reason=text)),gaps=rows(dict(mechanism_ref=text,reason=text)))
    return dict(type=['object','null'],properties=fields,required=list(fields),additionalProperties=False,
        description='W scientific comparison preview, separate from the complete result.candidates retention pool. '
        'Each unwaived mechanism in the frozen candidate_nomination_mechanisms needs one representative or an explicit gap. Local tasks do not reassess other mechanisms; their prior declarations and gaps remain visible to A. '
        'Use a listed historical candidate reference, or {call_id,artifact} for an exact candidate declared in your current managed call. '
        'Explain retention and mechanism coverage; repeated use of one model across mechanisms needs a scientific reason for each. '
        'Extras are distinct alternatives within the frozen extra_slots allowance. Never invent a model to fill a gap. '
        'A can read and choose any registered pool candidate without a new experiment or changing this declaration.')


def applicable(job):
    return job['role']=='W' and bool(job.get('candidate_nomination_mechanisms'))


def resolve(root, job, declaration, *, source=None):
    """Resolve only the declared candidate slots, preserving unresolvable advice."""
    import jsonschema
    if not isinstance(declaration,dict) or not jsonschema.Draft202012Validator(schema()).is_valid(declaration):return declaration
    from . import candidate_refs, generic_results, history, originals
    value=copy.deepcopy(declaration);current=None;retained=None
    for row in [*value.get('representatives',[]),*value.get('extras',[])]:
        if not isinstance(row,dict):continue
        candidate=row.get('candidate')
        if isinstance(candidate,str):
            try:row['candidate']=candidate_refs.resolve_reference(root,job,candidate,source=source)
            except ValueError:pass
        elif isinstance(candidate,dict) and (root is not None or source is not None and source.inherited):
            if current is None:
                if source is not None and source.inherited:
                    if originals.source_job(source)!=job:
                        raise ValueError('Candidate nominations differ from their exact source task')
                    seal=c.read(originals.source_record(source,'result-identity.json'))
                    observed=history.validate_observation_original(source,job,seal,controls_only=True)
                    current=(observed or {}).get('managed_calls',[])
                    retained=({row['id'] for row in (observed or {}).get('candidates',[])}
                        & {row['id'] for row in generic_results.candidates(current)})
                else:
                    seal_path=c.host_record(c.workspace(root,job['step']))/'result-identity.json'
                    receipt=c.read(c.regular(seal_path)).get('observations') if seal_path.exists() else None
                    current=(c.read(c.regular(root/receipt['path']))['managed_calls'] if receipt else history.managed_calls(root,job))
            matches=[generic_results.identity(call,item) for call in current
                if call['call_id']==candidate.get('call_id') for item in generic_results.declarations(call)
                if isinstance(item,dict) and item.get('artifact')==candidate.get('artifact')]
            if len(matches)==1 and (retained is None or matches[0] in retained):row['candidate']=matches[0]
    return value


def require(root, job, payload):
    """Repair missing declarations, never require a scientifically successful model."""
    if not applicable(job):return
    from . import generic_results, history
    value=c.scientific_field(job,payload,'candidate_nominations')
    def repair(message):
        raise OutputRepairRequired('candidate_nominations_incomplete','Correct candidate_nominations: '+message+
            '. '+SCOPE_GUIDANCE+' Reuse retained originals; do not rerun or resubmit experiments.')
    if value is None:repair('declare representatives, extras and explicit gaps')
    required={row['mechanism_ref'] for row in job['candidate_nomination_mechanisms'] if row['status']!='waived'}
    known=set(job['history']['candidate_ids'])
    known.update(row['id'] for row in generic_results.candidates(history.managed_calls(root,job)))
    seen=set();representatives=set()
    for row in value['representatives']:
        ref=row['mechanism_ref'];candidate=row['candidate']
        if ref not in required or ref in seen:repair('use each assigned unwaived mechanism exactly once as representative or gap')
        if not isinstance(candidate,str) or candidate not in known:repair('locate each representative in the exact retained candidate pool')
        seen.add(ref);representatives.add(candidate)
    for row in value['gaps']:
        ref=row['mechanism_ref']
        if ref not in required or ref in seen:repair('use each assigned unwaived mechanism exactly once as representative or gap')
        seen.add(ref)
    if seen!=required:repair('cover every assigned unwaived mechanism with a representative or an explained gap')
    extras=[]
    for row in value['extras']:
        candidate=row['candidate']
        if not isinstance(candidate,str) or candidate not in known:repair('locate each extra in the exact retained candidate pool')
        if candidate in representatives or candidate in extras:repair('extras must be distinct from representatives and other extras')
        extras.append(candidate)
    if len(extras)>job['candidate_nomination_policy']['extra_slots']:
        repair('keep extras within the frozen extra_slots allowance; the complete pool remains available to A')


def project(records, state):
    """Latest sealed declaration per mechanism, retaining each original author."""
    workers=[row for row in records if row['role']=='W'
        and isinstance(row.get('candidate_nominations'),dict)]
    latest=workers[-1] if workers else None
    mechanisms=state['mechanisms'] if state else {}
    declared={};sources={}
    for worker in workers:
        for kind in ('representatives','gaps'):
            for row in worker['candidate_nominations'][kind]:
                ref=row['mechanism_ref']
                declared[ref]=(kind,copy.deepcopy(row))
                sources[ref]=worker['step']
    value=dict(representatives=[],extras=copy.deepcopy(latest['candidate_nominations']['extras']) if latest else [],gaps=[])
    for kind,row in declared.values():value[kind].append(row)
    pending=[ref for ref,item in mechanisms.items() if item['status']!='waived' and ref not in declared]
    waived=[dict(mechanism_ref=ref,reason=item['disposition']['reason'],source_a=item['disposition']['source_a'])
        for ref,item in mechanisms.items() if item['status']=='waived' and item.get('disposition')]
    return dict(source_step=latest['step'] if latest else None,**value,pending_mechanisms=pending,waived=waived,
        mechanism_sources=sources)


def page_rows(value):
    rows=[]
    for kind in ('representatives','extras','gaps'):
        for item in value[kind]:
            source_step=value.get('mechanism_sources',{}).get(item.get('mechanism_ref'),value['source_step'])
            row=dict(kind=kind,source_step=source_step,**item)
            if 'candidate' in row:row['candidate_id']=row.pop('candidate')
            row['original']=f'inputs/history/nodes/{source_step:03d}/0001.json'
            reason=row['reason'];row['reason']=reason.encode()[:512].decode('utf-8',errors='ignore')
            row['reason_truncated']=row['reason']!=reason
            rows.append(row)
    rows.extend(dict(kind='pending',mechanism_ref=ref) for ref in value['pending_mechanisms'])
    for item in value['waived']:
        reason=item['reason'];excerpt=reason.encode()[:512].decode('utf-8',errors='ignore')
        rows.append(dict(item,kind='waived',reason=excerpt,reason_truncated=excerpt!=reason,
            original=f'inputs/history/nodes/{item["source_a"]:03d}/scientific-original.json'))
    return rows


def preview(value, index):
    return dict(source_step=value['source_step'],index=index,
        representatives=[dict(mechanism_ref=row['mechanism_ref'],candidate_id=row['candidate']) for row in value['representatives']],
        extras=[dict(candidate_id=row['candidate']) for row in value['extras']],
        gaps=[row['mechanism_ref'] for row in value['gaps']],pending_mechanisms=value['pending_mechanisms'],
        distinct_candidate_count=len({row['candidate'] for row in [*value['representatives'],*value['extras']]}),
        scope='Latest W scientific declaration per mechanism, with each original source retained. Other mechanisms keep their prior declarations or explicit pending gaps; no new global judgment is inferred. Read nomination pages for reasons; the complete candidate index remains selectable by A.')


def final_coverage_schema():
    """A owns coverage depth; control checks exact declaration and citations."""
    text=dict(type='string',pattern=r'\S')
    ref=dict(type='object',properties=dict(source_step=dict(type='integer',minimum=1),
        pointer=dict(type='string',pattern=r'^/(?:[^~]|~[01])*$')),required=['source_step','pointer'],additionalProperties=False)
    fields=dict(mechanism_ref=text,status=dict(type='string',enum=['deep','screened_pending','ruled_out','unexplored']),
        reason=text,science_refs=dict(type='array',minItems=1,items=ref),
        unanswered_questions=dict(type='array',items=text))
    return dict(type=['array','null'],items=dict(type='object',properties=fields,required=list(fields),additionalProperties=False),
        description='Required when ending with finish/deliver or returning FINAL. Cover every frozen final_coverage_mechanisms entry exactly once, including waived. Author the depth, reasons and exact visible scientific references; explicitly list unanswered questions (empty if none). State input/resource limits in limitations. Candidate existence and calls do not establish depth. Null for nonfinal turns. No new experiment is authorized.')


def require_final_coverage(root,job,payload):
    if (job['role']!='A' or job.get('formal_review') or job.get('purpose')=='formal_review'
            or not job.get('final_coverage_mechanisms')):return
    final=bool(job.get('final_report') or job.get('final_only') or job.get('purpose')=='closeout')
    if payload.get('next_action') in {'wait','audit','formal_review'}:return
    if not final and payload.get('next_action') not in {'finish','deliver'}:return
    import jsonschema
    from . import search
    def repair(detail):
        raise OutputRepairRequired('final_coverage_incomplete','Correct final_coverage in this same A output: '+detail+
            '. Preserve originals and report limitations; do not rerun experiments or start new scientific work.')
    value=payload.get('final_coverage')
    if value is None or not c.json_field_representable('final_coverage',value) or not jsonschema.Draft202012Validator(final_coverage_schema()).is_valid(value):
        repair('declare authored coverage, reason, scientific references and explicit unanswered_questions for every frozen mechanism')
    expected={row['mechanism_ref'] for row in job['final_coverage_mechanisms']}
    names=[row['mechanism_ref'] for row in value]
    if len(names)!=len(set(names)) or set(names)!=expected:repair('cover exactly the complete frozen mechanism list, including waived, without unknown or duplicate rows')
    try:search.validate_science_refs(root,job,[ref for row in value for ref in row['science_refs']])
    except ValueError:repair('science_refs must resolve within exact visible scientific originals')
    return value
