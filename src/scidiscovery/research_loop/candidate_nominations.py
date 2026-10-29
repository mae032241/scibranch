"""Authored W comparison previews over the complete retained candidate pool.

Recognizable preview entries resolve existing candidate identities. Mechanism
membership, suitability and reasons remain scientific judgments for W and A.
"""
from __future__ import annotations

import copy

from . import contracts as c


SCOPE_GUIDANCE = 'Optional candidate previews do not replace the complete retained candidate pool.'


def configuration(extra_slots):
    if type(extra_slots) is not int or extra_slots < 0:
        raise ValueError('Candidate extra slots must be a nonnegative integer')
    return dict(version=1, extra_slots=extra_slots)


def schema():
    return dict(type=['object','null'],description='Optional scientific candidate preview; the complete retained candidate pool remains selectable.')


def _preview(declaration):
    """Read recognizable preview entries without imposing a scientific contract."""
    if not isinstance(declaration,dict):return None
    result={key:[] for key in ('representatives','extras','gaps')}
    for kind in result:
        rows=declaration.get(kind,[])
        if not isinstance(rows,list):continue
        for row in rows:
            if not isinstance(row,dict):continue
            if kind!='extras' and not isinstance(row.get('mechanism_ref'),str):continue
            candidate=row.get('candidate')
            if kind!='gaps' and not (isinstance(candidate,str) or isinstance(candidate,dict)
                    and all(isinstance(candidate.get(key),str) for key in ('call_id','artifact'))):continue
            item=dict(reason=row.get('reason') if isinstance(row.get('reason'),str) else '')
            if kind!='extras':item['mechanism_ref']=row['mechanism_ref']
            if kind!='gaps':item['candidate']=copy.deepcopy(candidate)
            result[kind].append(item)
    return result


def applicable(job):
    return job['role']=='W' and bool(job.get('candidate_nomination_mechanisms'))


def resolve(root, job, declaration, *, source=None):
    """Resolve only the declared candidate slots, preserving unresolvable advice."""
    value=_preview(declaration)
    if value is None:return None
    from . import candidate_refs, generic_results, history, originals
    value=copy.deepcopy(value);current=None;retained=None
    for row in [*value.get('representatives',[]),*value.get('extras',[])]:
        if not isinstance(row,dict):continue
        candidate=row.get('candidate')
        if isinstance(candidate,str):
            try:row['candidate']=candidate_refs.resolve_reference(root,job,candidate,source=source)
            except ValueError:row['candidate']=None
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
    """Candidate previews are optional advice, never an output-repair gate."""
    return None


def project(records, state):
    """Latest sealed declaration per mechanism, retaining each original author."""
    workers=[dict(row,candidate_nominations=value) for row in records if row['role']=='W'
        and (value:=_preview(row.get('candidate_nominations'))) is not None]
    latest=workers[-1] if workers else None
    mechanisms=state['mechanisms'] if state else {}
    declared={};sources={}
    for worker in workers:
        for kind in ('representatives','gaps'):
            for row in worker['candidate_nominations'][kind]:
                if kind=='representatives' and not isinstance(row.get('candidate'),str):continue
                ref=row['mechanism_ref']
                declared[ref]=(kind,copy.deepcopy(row))
                sources[ref]=worker['step']
    value=dict(representatives=[],extras=[copy.deepcopy(row) for row in latest['candidate_nominations']['extras'] if isinstance(row.get('candidate'),str)] if latest else [],gaps=[])
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




def require_final_coverage(root,job,payload):
    """Require a brief authored account of every frozen final mechanism."""
    if (job['role']!='A' or job.get('formal_review') or job.get('purpose')=='formal_review'
            or not job.get('final_coverage_mechanisms')):return
    if payload.get('next_action') in {'wait','audit','formal_review'}:return
    final=bool(job.get('final_report') or job.get('final_only') or job.get('purpose')=='closeout')
    if not final and payload.get('next_action') not in {'finish','deliver'}:return
    from .output_repair import OutputRepairRequired
    def reject(detail):
        raise OutputRepairRequired('final_coverage_incomplete','Correct final_coverage in this same A output: '+detail+
            '. Preserve originals and report limitations; no new scientific work is authorized.')
    value=payload.get('final_coverage')
    if not isinstance(value,list):reject('give mechanism_ref, status and a nonblank reason for every frozen mechanism')
    expected={row['mechanism_ref'] for row in job['final_coverage_mechanisms']};seen=set()
    for row in value:
        if (not isinstance(row,dict) or not isinstance(row.get('mechanism_ref'),str) or row['mechanism_ref'] not in expected
                or row['mechanism_ref'] in seen or not isinstance(row.get('status'),str)
                or row['status'] not in {'deep','screened_pending','ruled_out','unexplored'}
                or not isinstance(row.get('reason'),str) or not row['reason'].strip()):
            reject('cover each exact mechanism once with its authored status and reason')
        seen.add(row['mechanism_ref'])
    if seen!=expected:reject('cover the complete frozen mechanism list, including waived routes')
    return value
