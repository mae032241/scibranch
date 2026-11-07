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


def _preview(declaration, *, available=None):
    """Read recognizable preview entries without imposing a scientific contract."""
    result={key:[] for key in ('representatives','extras','gaps')}
    diagnostics=[]
    def unreadable(pointer):
        diagnostics.append(dict(code='candidate_nomination_unresolved',severity='warning',pointer=pointer,
            reason='Optional nomination preview could not resolve this declaration; inspect the scientific original.'))
    if not isinstance(declaration,dict):
        if not declaration:return None
        unreadable('/candidate_nominations')
        return dict(result,diagnostics=diagnostics)
    for kind in result:
        rows=declaration.get(kind,[])
        if not isinstance(rows,list):
            if rows is not None:unreadable('/candidate_nominations/'+kind)
            continue
        skipped=False
        for row in rows:
            if not isinstance(row,dict):skipped=True;continue
            if kind!='extras' and not isinstance(row.get('mechanism_ref'),str):skipped=True;continue
            candidate=row.get('candidate')
            if kind!='gaps' and not isinstance(candidate,str):skipped=True;continue
            if kind!='gaps' and available is not None and candidate not in available:
                skipped=True;continue
            item=dict(reason=row.get('reason') if isinstance(row.get('reason'),str) else '')
            if kind!='extras':item['mechanism_ref']=row['mechanism_ref']
            if kind!='gaps':item['candidate']=copy.deepcopy(candidate)
            result[kind].append(item)
        if skipped:unreadable('/candidate_nominations/'+kind)
    if any(key not in result for key in declaration) and not any(result.values()):
        unreadable('/candidate_nominations')
    return dict(result,diagnostics=diagnostics)


def applicable(job):
    return job['role']=='W' and bool(job.get('candidate_nomination_mechanisms'))


def resolve(root, job, declaration, *, source=None, inventory=None, source_facts=None):
    """Resolve only the declared candidate slots, preserving unresolvable advice."""
    if not isinstance(declaration,dict):return copy.deepcopy(declaration)
    from . import candidate_refs, generic_results, handoff_state, originals
    value=copy.deepcopy(declaration);current=None;retained=None
    for row in [row for kind in ('representatives','extras')
            for row in (value.get(kind,[]) if isinstance(value.get(kind),list) else [])]:
        if not isinstance(row,dict):continue
        candidate=row.get('candidate')
        if isinstance(candidate,str):
            try:row['candidate']=candidate_refs.resolve_reference(root,job,candidate,source=source)
            except ValueError:row['candidate']=None
        elif (isinstance(candidate,dict) and all(isinstance(candidate.get(key),str) for key in ('call_id','artifact'))
                and (root is not None or source is not None and source.inherited)):
            if current is None:
                if source_facts is not None:
                    if source_facts['job'] != job or (source is not None and source_facts['owner'] != source):
                        raise ValueError('Candidate nominations differ from their exact source task')
                    observed = source_facts['observation']
                    current = (observed or {}).get('managed_calls', [])
                    if source is not None and source.inherited:
                        retained = ({row['id'] for row in (observed or {}).get('candidates', [])}
                            & {row['id'] for row in generic_results.candidates(current)})
                elif source is not None and source.inherited:
                    with handoff_state.loading():
                        if originals.source_job(source)!=job:
                            raise ValueError('Candidate nominations differ from their exact source task')
                        seal=c.read(originals.source_record(source,'result-identity.json'))
                        observed=(c.read(originals.resolve_ref(source,seal['observations']))
                            if seal.get('observations') is not None else None)
                    current=(observed or {}).get('managed_calls',[])
                    retained=({row['id'] for row in (observed or {}).get('candidates',[])}
                        & {row['id'] for row in generic_results.candidates(current)})
                else:
                    seal_path=c.host_record(c.workspace(root,job['step']))/'result-identity.json'
                    if seal_path.exists():
                        receipt=c.read(c.regular(seal_path)).get('observations')
                        current=(c.read(c.regular(root/receipt['path']))['managed_calls'] if receipt else [])
                    else:
                        if inventory is None:
                            raise ValueError('Current candidate nominations require their accepted inventory')
                        current=inventory.managed_calls
            matches=[generic_results.identity(call,item) for call in current
                if call['call_id']==candidate.get('call_id') for item in generic_results.declarations(call)
                if isinstance(item,dict) and item.get('artifact')==candidate.get('artifact')]
            row['candidate']=(matches[0] if len(matches)==1 and (retained is None or matches[0] in retained) else None)
    return value


def require(root, job, payload, *, inventory):
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
    diagnostics=[dict(row,source_step=latest['step'],original=f'inputs/history/nodes/{latest["step"]:03d}/0001.json')
        for row in latest['candidate_nominations']['diagnostics']] if latest else []
    return dict(source_step=latest['step'] if latest else None,**value,pending_mechanisms=pending,waived=waived,
        mechanism_sources=sources,diagnostics=diagnostics)


def table_project(summary):
    """Project the same optional advice from the current small breadth snapshot."""
    table=summary['table'];mechanisms=table['mechanisms'];nominations=table['nominations']
    declared=nominations['by_mechanism_ref']
    value=dict(representatives=[],extras=copy.deepcopy(nominations['latest_extras']),gaps=[])
    sources={}
    for ref,item in sorted(declared.items(),key=lambda pair:pair[1]['order']):
        value[item['kind']].append(copy.deepcopy(item['declaration']))
        sources[ref]=item['source_step']
    diagnostics=[dict(row,source_step=nominations['source_step'],
        original=f'inputs/history/nodes/{nominations["origin_identity"]["goal_id"]}/{nominations["source_step"]:03d}/science.json')
        for row in nominations.get('diagnostics',[])]
    return dict(source_step=nominations['source_step'],**value,diagnostics=diagnostics,
        pending_mechanisms=[ref for ref,item in mechanisms.items() if item['status']!='waived' and ref not in declared],
        waived=[dict(mechanism_ref=ref,reason=item['disposition']['reason'],source_a=item['disposition']['source_a'])
            for ref,item in mechanisms.items() if item['status']=='waived' and item.get('disposition')],
        mechanism_sources=sources)


def source_table_projection(summary,resolve_public):
    """Public nomination detail; candidate mapping is an explicit caller read."""
    value=table_project(summary);stored=summary['table']['nominations']
    result=dict(source_step=value['source_step'],representatives=[],extras=[],gaps=[],
        pending_mechanisms=value['pending_mechanisms'],waived=value['waived'],diagnostics=value['diagnostics'])
    for kind in ('representatives','gaps','extras'):
        for declaration in value[kind]:
            record=stored if kind=='extras' else stored['by_mechanism_ref'][declaration['mechanism_ref']]
            row=copy.deepcopy(declaration);step=record['source_step']
            row.update(source_step=step,original=f'inputs/history/nodes/{record["origin_identity"]["goal_id"]}/{step:03d}/science.json')
            reason=row['reason'];row['reason']=reason.encode()[:512].decode('utf-8',errors='ignore')
            row['reason_truncated']=row['reason']!=reason
            if 'candidate' in row:row['candidate_id']=resolve_public(row.pop('candidate'))
            result[kind].append(row)
    return result


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
    rows.extend(dict(row,kind='diagnostic') for row in value.get('diagnostics',[]))
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
        diagnostics=copy.deepcopy(value.get('diagnostics',[])),
        scope='Latest W scientific declaration per mechanism, with each original source retained. Other mechanisms keep their prior declarations or explicit pending gaps; no new global judgment is inferred. Read nomination pages for reasons; the complete candidate index remains selectable by A.')




def require_final_coverage(root,job,payload):
    """Return optional coverage without requiring a fabricated global inventory."""
    return payload.get('final_coverage')
