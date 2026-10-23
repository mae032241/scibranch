"""Rebuildable declared relationships from sealed science and plugin facts.

This module never executes a domain experiment or imports a domain adapter.
"""
from __future__ import annotations

from . import configuration_updates

import copy
import json
from pathlib import Path

from . import contracts as c, history, originals, search

MAX_CONTEXTS = 128


def context_artifacts(item):
    """Top-level proof descriptors have provider-owned scientific labels."""
    return [value for value in item.values() if isinstance(value,dict)
        and {'path','size_bytes'} <= set(value)]


def _parent_original(root, item, available, *, source=None):
    """Bind the declared choice to an earlier exact completed call, never a name."""
    accepted=next((p for p in item['proofs'] if Path(p['path']).name=='accepted.json'),None)
    if accepted is None or accepted.get('retention')!='archived':
        raise ValueError('Accepted context original unavailable')
    path=dict(accepted,path=accepted['snapshot'])
    original=c.read(originals.resolve_ref(source,path) if source is not None else originals.resolve_root_ref(root,path))
    if source is not None and source.inherited:
        from . import candidate_refs
        original=copy.deepcopy(original)
        parent=original.get('parent')
        if parent is not None and parent.get('candidate_id') is not None:
            identity=parent['candidate_id']
            mapping=candidate_refs.source_input_mapping(source,originals.source_job(source))
            parent['candidate_id']=(candidate_refs.origin_candidate(source.origin_identity,identity)
                if parent['source']=='same_node_completed_call' else mapping.get(identity,identity))
    for key in ('call_id','scope','parent','change_intent'):
        if original.get(key)!=item[key]:
            raise history.ObserverIntegrityError('Context differs from accepted original')
    parent=item['parent'];binding=original.get('parent_binding')
    if parent['status']!='declared':
        if binding is not None:raise history.ObserverIntegrityError('Undeclared parent acquired a binding')
        return
    source=available.get(parent['candidate_id'])
    if source is None or source['status']!='known_success':
        raise history.ObserverIntegrityError('Declared parent has no earlier completed original')
    completion=next((p for p in source['proofs'] if Path(p['path']).name=='completed.json'),None)
    if completion is None:raise history.ObserverIntegrityError('Parent completion original unavailable')
    same=source['scope']['step']==item['scope']['step']
    if (not isinstance(binding,dict) or any(binding.get(key)!=value for key,value in
            dict(call_id=source['call_id'],step=source['scope']['step'],completion_ref=dict(path=str(Path(source['scope']['job_ref']['path']).parent.parent / completion['path']),size_bytes=completion['size_bytes'])).items())
            or parent['source']!=('same_node_completed_call' if same else 'sealed')
            or source['scope']['step']>item['scope']['step']):
        raise history.ObserverIntegrityError('Declared parent completion/source differs')
    return original, source


def contexts(root, job, items, archive, previous=()):
    """Validate plugin v2 against the actual frozen task/attempt before sealing."""
    if not isinstance(items,list):
        raise ValueError('Execution context list required')
    from .protocol import stage_scope
    expected_scope=stage_scope(root,job)
    result=[];seen=set();available={item['candidate_id']:item for item in previous if item['status']=='known_success'}
    for item in items:
        if not isinstance(item,dict) or not {'call_id','scope','status','candidate_id','parent','change_intent','note','proofs'} <= set(item):
            raise ValueError('Plugin context fields differ from contract2')
        if not isinstance(item['call_id'],str) or len(item['call_id'])>128 or item['call_id'] in seen:
            raise history.ObserverIntegrityError('Execution context identity is ambiguous')
        seen.add(item['call_id']);scope=item['scope']
        if not isinstance(scope,dict) or scope!=expected_scope:
            raise history.ObserverIntegrityError('Execution context differs from frozen scientific task')
        if item['status'] not in {'known_success','known_failed','unknown'}:
            raise ValueError('Explicit known failed/unknown execution status required')
        parent=item['parent']
        if (not isinstance(parent,dict) or set(parent)!={'status','candidate_id','source'}
                or parent['status'] not in {'declared','declared_root','unknown'}
                or parent['source'] not in {'sealed','same_node_completed_call','none'}
                or (parent['status']=='declared')!=(parent['candidate_id'] is not None)):
            raise ValueError('Declared parent choice differs from contract2')
        if not isinstance(item['proofs'],list) or not item['proofs']:
            raise ValueError('Context originals are missing')
        proofs=[]
        for proof in item['proofs']:
            if not isinstance(proof,dict) or set(proof)!={'path','size_bytes'}:
                raise ValueError('Context original shape differs')
            original=archive(proof['path'])
            if original is None:raise ValueError('Context archive read capacity unavailable')
            if not history._same_original(root, original, proof):
                raise history.ObserverIntegrityError('Execution context original changed')
            proofs.append(original)
        if (job.get('search') or {}).get('breadth'):
            from . import breadth
            original=next((proof for proof in proofs if Path(proof['path']).name=='accepted.json'),None)
            if original is None or original.get('retention')!='archived':raise history.ObserverIntegrityError('Accepted call original missing')
            accepted=c.read(root/original['snapshot'])
            binding=item.get('mechanism_binding')
            if accepted.get('scope')!=scope or accepted.get('call_id')!=item['call_id'] or accepted.get('mechanism_binding')!=binding:
                raise history.ObserverIntegrityError('Context mechanism differs from original accepted call')
            breadth.check_binding(job['search']['breadth'],binding,scope)
        # Provider-owned artifact labels are opaque; every descriptor receives
        # the same original-byte validation, regardless of its scientific name.
        validated={**item,'proofs':proofs}
        for key,value in item.items():
            if not isinstance(value,dict) or not {'path','size_bytes'} <= set(value):continue
            if set(value)!={'path','size_bytes'}:
                raise ValueError('Provider context cannot predeclare host archive fields')
            original=archive(value['path'])
            if original is None:raise ValueError('Context artifact read capacity unavailable')
            if any(original[field]!=value[field] for field in ('size_bytes',)):
                raise history.ObserverIntegrityError('Execution artifact changed')
            validated[key]=original
        _parent_original(root,validated,available)
        accepted_proof=next((proof for proof in proofs if Path(proof['path']).name=='accepted.json'),None)
        accepted=c.read(root/accepted_proof['snapshot'])
        interface=history._execution_interface(root,accepted)
        from . import experiments
        declared,_=experiments.registration(interface['executor'])
        if declared.context_validator is not None:
            declared.context_validator(validated,accepted,available.get(item['parent']['candidate_id']))
        result.append(validated)
        if validated['status']=='known_success':available[validated['candidate_id']]=validated
    return result


def build(root, before_step, *, sources=None, search_state=None):
    """Fold relations without rendering a default packet or updating authority."""
    sources=history.source_sequence(root,before_step) if sources is None else sources
    if search_state is None:
        from . import execution_recovery
        recovery_refs = (originals.frozen_recovery_refs(root, before_step) if (Path(root) / originals.MANIFEST).exists()
            else execution_recovery.prefix(root,before_step))
        state=search.fold(root,sources,recovery_refs=recovery_refs)
    else:
        state=search_state
    nodes=[];candidates={};edges=[];calls=set();coverage='complete';available={}
    for job,payload,seal,_ in sources:
        step=job['step'];owner=originals.node(root,step)
        nodes.append(dict(step=step,role=job['role'],parents=[int(Path(r['path']).parts[2]) for r in job['direct_refs']],
                          source_ref=seal['raw_ref'],summary=payload['summary'],limitations=payload['limitations']))
        for selected in seal.get('selected_inputs',[]):
            edges.append(dict(kind='reuse',subject=dict(node=step),target=dict(node=selected['origin_step'],material=selected['material']),scope='declared_handoff_not_proven_read'))
        receipt=seal.get('observations')
        if not receipt:coverage='partial';continue
        observation=history.read_observation(root, job, seal)
        if observation['coverage']['status']!='complete':coverage='partial'
        for row in observation['candidates']:
            if row['id'] in candidates and candidates[row['id']]['observation']!=row:
                raise ValueError('Candidate identity changed')
            candidates.setdefault(row['id'],dict(observation=row,mechanism_ref=None,parent=None,derivation='unknown',support='unknown'))
        for context in observation.get('contexts',[]):
            identity=(originals.identity_key(owner.origin_identity),context['call_id'])
            if identity in calls:raise ValueError('Context was sealed more than once')
            calls.add(identity);candidate=context['candidate_id']
            _parent_original(root,context,available,source=owner)
            if context['status']=='known_success':available[candidate]=context
            if context['status']=='unknown':coverage='partial';continue
            if candidate not in candidates:raise ValueError('Context has no observed candidate')
            parent=context['parent'];item=candidates[candidate]
            item.update(execution_status=context['status'],mechanism_ref=(context.get('mechanism_binding') or {}).get('mechanism_ref',context['scope']['mechanism_ref']),
                        parent=parent['candidate_id'],derivation=parent['status'],context=context)
            if context['status']=='known_success' and parent['status']=='declared':
                if parent['candidate_id'] not in candidates:raise ValueError('Declared parent has no exact original')
                edges.append(dict(kind='derivation',subject=candidate,target=parent['candidate_id'],scope='declared_not_proven_causality',call_id=context['call_id']))
    # Candidate ancestry is a DAG; names never supply missing historical edges.
    parents={edge['subject']:edge['target'] for edge in edges if edge['kind']=='derivation'}
    for candidate in parents:
        visiting=set();current=candidate
        while current in parents:
            if current in visiting:raise ValueError('Candidate derivation cycle')
            visiting.add(current);current=parents[current]
    return dict(nodes=nodes,mechanisms=copy.deepcopy(state['mechanisms']),candidates=candidates,relations=edges,
                coverage=coverage,limitation='Declared choices and observed call bindings; no inherited scientific or audit qualification')


def candidate_view(records):
    candidates, current, actual, coverage = {}, None, None, None
    for record in records:
        observation = record['observation']
        if observation is not None:
            coverage = observation.get('coverage')
            for candidate in observation['candidates']:
                # Same scientific ID cannot silently point to another implementation
                # or evaluation. Metric updates require a different candidate ID.
                previous = candidates.get(candidate['id'])
                view = record.get('view', {})
                owner_step = int(candidate['implementation'].split('/')[2]) if candidate['implementation'] else None
                owner = next((row for row in records if row['step'] == owner_step), None)
                item = {**candidate, 'observation_step': record['step'], 'observation_h_scope': view.get('h_scope'),
                        'observation_summary': view.get('summary', '')[:160],
                        'observation_conclusion': next(iter(view.get('conclusion_pages', [])), None),
                        'implementation_owner_step': owner_step,
                        'implementation_conclusion': next(iter(owner.get('view', {}).get('conclusion_pages', [])), None) if owner else None,
                        'candidate_h_scope': None, 'parent_candidate': None, 'candidate_action': None}
                if previous and {k: previous[k] for k in candidate} != candidate:
                    raise ValueError('Observed candidate identity changed')
                candidates.setdefault(candidate['id'], item)
            current = (dict(candidate_id=observation['current_candidate']['candidate_id'], observed_at=record['step'])
                       if observation.get('current_candidate') is not None else None)
            if observation['actual_submission'] is not None:
                actual = dict(candidate_id=observation['actual_submission']['candidate_id'],
                              observed_at=record['step'], retention=observation['actual_submission'].get('artifact', {}).get('retention', 'unknown'))
        if record['recommendation'] is not None:
            if record['recommendation'] not in candidates:
                raise ValueError('A recommended an unknown candidate')
    def public(item):
        return {key: item[key] for key in ('id', 'label', 'implementation', 'evaluation_key', 'metric', 'direction', 'status', 'observation_step', 'observation_h_scope', 'observation_summary', 'observation_conclusion', 'implementation_owner_step', 'implementation_conclusion', 'candidate_h_scope', 'parent_candidate', 'candidate_action')}
    def compact(item):
        view = public(item)
        for key, capacity in (('label', 160), ('evaluation_key', 192), ('observation_summary', 160)):
            text = view[key]
            view[key] = text.encode()[:capacity].decode('utf-8', errors='ignore') if isinstance(text,str) else text
            view[key + '_truncated'] = view[key] != text
        if view['implementation'] is not None and len(view['implementation'].encode()) > 512:
            view['implementation'] = None
        view['details_in_index'] = True
        return view
    from . import selection
    return dict(coverage=coverage, current_candidate=current, candidate_index=[public(row) for row in candidates.values()],
                **selection.recommendations(records), actual_submission=actual,
                candidate_facts=[compact(row) for row in list(candidates.values())[:16]],
                candidates_omitted_count=max(0, len(candidates) - 16),
                limitation='Candidate facts retain first-observation order without ranking. Models are retained only as sealed artifacts or explicit archived snapshots. Read the complete candidate index and each original scientific conclusion for applicability and blind spots. Null slots mean unknown, not agreement.')



def _clip(text, capacity):
    return text.encode()[:capacity].decode('utf-8', errors='ignore')


def project(root, before_step, *, source_cutoff=None, extra_steps=()):
    """One verified source collection feeds all derived scientific navigation."""
    sources=history.source_sequence(root,before_step,source_cutoff=source_cutoff,extra_steps=extra_steps)
    from . import execution_recovery
    recovery_refs = (originals.frozen_recovery_refs(root, before_step) if (Path(root) / originals.MANIFEST).exists()
        else execution_recovery.prefix(root,before_step))
    records=execution_recovery.project_records(root,history.records_from_sources(root,sources,write_cache=False),recovery_refs,sources=sources)
    state=search.fold(root,sources,recovery_refs=recovery_refs) if configuration_updates.for_new_job(root,before_step).get('search_policy') is not None else None
    graph=build(root,before_step,sources=sources,search_state=state)
    return dict(sources=sources,records=records,search_state=state,graph=graph,candidates=candidate_view(records),recovery_refs=recovery_refs)


def brief(records, state, graph, candidates, indexes, *, selection_protocol=0):
    """The complete default lineage packet is bounded using its written bytes."""
    recent=[]
    for record in records[-2:]:
        view=record['view']
        recent.append(dict(step=record['step'],role=record['role'],summary=_clip(view['summary'],160),
            summary_truncated=view['summary_omitted_chars']>0 or len(view['summary'].encode())>160,
            limitation=_clip(next(iter(view['limitations']),''),96),
            limitations_omitted_count=max(0,len(view['limitations'])-1)+view['limitations_omitted_count'],
            original=view['conclusion_pages'][0]))
    focus=state['focus'] if state else None
    pending=[ref for ref,item in state['mechanisms'].items() if item['status']=='pending'] if state else []
    value=dict(indexes=indexes,node_count=len(records),recent=recent,
        mechanisms=dict(availability='available' if state else 'unavailable',count=len(state['mechanisms']) if state else 0,pending_count=len(pending),
                        focus={k:v for k,v in focus.items() if k!='repair_source'} if focus else None),
        candidates=dict(count=len(candidates['candidate_index']),coverage=(dict(status=candidates['coverage']['status'],
            note=_clip(candidates['coverage']['note'],96),note_truncated=len(candidates['coverage']['note'].encode())>96) if candidates['coverage'] else None),
            current=candidates['current_candidate'],a_recommended=candidates['a_recommended'],
            historical_a_recommendation=candidates.get('historical_a_recommendation'),actual=candidates['actual_submission'],
            candidate_facts=[{k:item[k] for k in ('id','evaluation_key','metric','direction','status')} for item in candidates['candidate_facts'][:2]],
            candidate_facts_omitted_count=len(candidates['candidate_index'])-min(2,len(candidates['candidate_facts']))),
        relationships=dict(count=len(graph['relations']),coverage=graph['coverage'],scope='Declared derivation and reuse; support remains unknown without original scientific intent.'),
        access='Read index/next_page and original conclusion pages as needed. Unselected historical attachments are not mounted. '
               'Implementation IDs select exact objects; null keeps current selected inputs, [] clears, lists replace. '
               'Candidate facts, authored recommendation and actual submission are independent; unknown/partial coverage is not a complete tree.')
    # Large user labels/focus are navigation excerpts, never a license to drop bounds.
    if value['mechanisms']['focus']:
        for key,text in list(value['mechanisms']['focus'].items()):
            if isinstance(text,str):value['mechanisms']['focus'][key]=_clip(text,80)
    for row in value['candidates']['candidate_facts']:
        if isinstance(row['evaluation_key'],str):row['evaluation_key']=_clip(row['evaluation_key'],192)
        row['evaluation_key_truncated']=any(item['id']==row['id'] and item['evaluation_key']!=row['evaluation_key'] for item in candidates['candidate_facts'])
    if selection_protocol == 1:
        from . import selection
        try:
            public = selection.project(selection.public_inputs(records))
        except ValueError:
            # Sources were verified before this optional projection. No broad
            # catch may cover source/hash/permission checks or scientific seals.
            public = dict(scope='unavailable', candidate_count=len(candidates['candidate_index']),
                coverage='unknown', current_candidate=None, a_recommended=None, historical_a_recommendation=None,
                observed_actual_submission=None, delivery=dict(state='unknown', current_confirmed=None, last_confirmed=None),
                candidate_facts=[], candidate_facts_omitted_count=len(candidates['candidate_index']), public_audits=[], audits_omitted_count=0,
                limitation='Public selection overview unavailable; inspect existing candidate and original conclusion indexes.')
        value['candidates'] = dict(public, selection_protocol=1)
    elif selection_protocol != 0:
        raise ValueError('Unsupported frozen selection protocol')
    return bounded_brief(value)


def bounded_brief(value):
    """Drop only navigation excerpts with explicit omissions, never bound identities."""
    value['recent_omitted_count']=value['node_count']-len(value['recent'])
    def size():return len((json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode())
    while size()>history.MAX_BRIEF_BYTES and value['recent']:
        value['recent'].pop(0);value['recent_omitted_count']+=1
    if value['candidates'].get('selection_protocol') == 1:
        for field, omitted in (('public_audits','audits_omitted_count'), ('candidate_facts','candidate_facts_omitted_count')):
            while size()>history.MAX_BRIEF_BYTES and value['candidates'].get(field):
                value['candidates'][field].pop();value['candidates'][omitted]+=1
    while size()>history.MAX_BRIEF_BYTES and value['candidates'].get('adapter_audits'):
        value['candidates']['adapter_audits'].pop();value['candidates']['adapter_audits_omitted_count']+=1
    order = value['mechanisms'].get('unfinished_plan')
    while size()>history.MAX_BRIEF_BYTES and order and len(order['remaining']) > 1:
        order['remaining'].pop();order['omitted_count']+=1
    while size()>history.MAX_BRIEF_BYTES and value['mechanisms'].get('overview'):
        value['mechanisms']['overview'].pop();value['mechanisms']['overview_omitted_count']+=1
    while size()>history.MAX_BRIEF_BYTES and value['candidates'].get('candidate_facts'):
        value['candidates']['candidate_facts'].pop();value['candidates']['candidate_facts_omitted_count']+=1
    nominations=value['candidates'].get('nominations')
    if size()>history.MAX_BRIEF_BYTES and nominations and 'representatives' in nominations:
        # Never silently truncate a scientific coverage declaration. The full
        # preview remains paginated; this navigation view points to all of it.
        value['candidates']['nominations']={key:nominations[key] for key in
            ('source_step','index','distinct_candidate_count','scope')}
        value['candidates']['nominations']['preview_in_index']=True
    while size()>history.MAX_BRIEF_BYTES and value.get('implementation_options'):
        value['implementation_options'].pop();value['implementation_options_omitted_count']+=1
    if size()>history.MAX_BRIEF_BYTES:
        # This is a disposable navigation view; source rows and indexes remain
        # authoritative. Oversized optional metadata must not reject a seal.
        original=value['candidates']
        value.update(recent=[],recent_omitted_count=value['node_count'],view_omitted=True,
            omission_reason='Navigation metadata exceeds the compact view; read indexes for original facts.',
            mechanisms=dict(availability=value['mechanisms']['availability'],count=value['mechanisms']['count'],
                pending_count=value['mechanisms']['pending_count'],focus=None),
            candidates=dict(count=original.get('count',original.get('candidate_count',0)),coverage=None,
                current=None,a_recommended=None,historical_a_recommendation=None,actual=None,
                candidate_facts=[],candidate_facts_omitted_count=original.get('candidate_facts_omitted_count',0)+len(original.get('candidate_facts',[])),
                selection_protocol=original.get('selection_protocol',0),public_audits=[],
                audits_omitted_count=original.get('audits_omitted_count',0)+len(original.get('public_audits',[]))))
        if nominations:
            value['candidates']['nominations']=dict(source_step=nominations['source_step'],index=nominations['index'],
                distinct_candidate_count=nominations['distinct_candidate_count'],preview_in_index=True)
    return value


def public_relations(graph, sources):
    originals={job['step']:(job,seal) for job,_,seal,_ in sources}
    rows=[]
    for edge in graph['relations']:
        if edge['kind']=='reuse':
            step=edge['target']['node'];job,seal=originals[step]
            materials=sorted(seal['materials'],key=lambda row:row['path'])
            index=next((i for i,row in enumerate(materials,1) if row==edge['target']['material']),None)
            if index is None:raise ValueError('Reuse relationship has no exact sealed implementation')
            rows.append(dict(kind='reuse',node=edge['subject']['node'],implementation_id=f'{job["role"]}{step:03d}-I{index:03d}',
                label=_clip(Path(materials[index-1]['path']).name,80),source_node=step,
                original=f'inputs/history/nodes/{step:03d}/0001.json',scope=edge['scope']))
        else:
            rows.append({key:edge[key] for key in ('kind','subject','target','scope')})
    return rows


def bind(root, step, role, protocol, *, source_cutoff=None, extra_steps=(), selection_protocol=0,
         audit_projection_version=0, adjacent_audit=None, worker_view_version=0):
    from .protocol import require_current
    require_current(root)
    projection=project(root,step,source_cutoff=source_cutoff,extra_steps=extra_steps)
    records,state=projection['records'],projection['search_state']
    from . import storage_policy as storage
    frozen=configuration_updates.for_new_job(root,step)
    context=storage.Deadline(frozen['max_seconds'])
    inventory=storage.Inventory(frozen.get('storage_policy'),context=context)
    def bind_original(source,destination,original_reference):
        inventory.entry();inventory.file(source.stat().st_size)
        c._bind_original(source, destination, root=root, policy=frozen.get('storage_policy'), context=context, size_bytes=(source).stat().st_size, source_kind='mutable')
    add,paged,files=history.page_writer(c.workspace(root,step),root=root,policy=frozen.get('storage_policy'),context=context)
    from . import candidate_refs
    references=candidate_refs.references(records) if worker_view_version==1 else {}
    if references:
        original_add,original_paged=add,paged
        def add(path,value):return original_add(path,candidate_refs.public_fields(value,references))
        def paged(rows,*args):return original_paged(candidate_refs.public_fields(rows,references),*args)
        for record,source in zip(records,projection['sources']):
            reported=candidate_refs.public_science_fields(source[1],references)
            original_path=f'inputs/history/nodes/{record["step"]:03d}/scientific-original.json'
            public=c.scientific_projection(reported,original_path)
            if public!=reported:
                raw=c.regular(c._execution_raw(root,record['step']))
                destination=c.workspace(root,step)/original_path
                bind_original(raw,destination,source[2]['raw_ref'])
                files.append(dict(path=original_path,size_bytes=destination.stat().st_size,
                    kind='scientific_original'))
            text=json.dumps(public,ensure_ascii=False,allow_nan=False)
            record['detail']=[dict(step=record['step'],role=record['role'],
                source='node:%03d/scientific-output' % record['step'],part=index+1,
                parts=(len(text)+history.TEXT_CHARS-1)//history.TEXT_CHARS,text=text[start:start+history.TEXT_CHARS])
                for index,start in enumerate(range(0,len(text),history.TEXT_CHARS))]
            record['view']['conclusion_pages']=[f'inputs/history/nodes/{record["step"]:03d}/{index+1:04d}.json'
                for index in range(len(record['detail']))]
    implementation_ids={}
    for record,source in zip(records,projection['sources']):
        job,_,seal,_=source
        materials=sorted(seal['materials'],key=lambda row:row['path'])
        options=[dict(id=f'{job["role"]}{job["step"]:03d}-I{number:03d}',label=_clip(Path(item['path']).name,80),
                      label_truncated=len(Path(item['path']).name.encode())>80)
                 for number,item in enumerate(materials,1)]
        implementation_ids.update((history.handle(material),option['id']) for material,option in zip(materials,options))
        paths=paged(options,16,'implementations',f'inputs/history/implementations/{record["step"]:03d}','sealed-implementation-objects')
        view=record['view'];view['implementation_index']=paths[0] if paths else None
        view['implementations']=options[:16];view['implementations_omitted_count']=max(0,len(options)-16)
        view['summary_truncated']=view['summary_omitted_chars']>0 or len(view['summary'].encode())>160
        view['summary']=_clip(view['summary'],160)
        view['limitations_truncated']=view['limitations_omitted_chars']>0 or any(len(text.encode())>96 for text in view['limitations'])
        view['limitations']=[_clip(text,96) for text in view['limitations']]
        for path,detail in zip(view['conclusion_pages'],record['detail']):add(path,detail)
    nodes=paged([row['view'] for row in records],8,'records','inputs/history/pages','sealed-scientific-nodes')
    overview=projection['candidates'];candidate_rows=overview['candidate_index']
    for row in candidate_rows:
        if row['implementation'] is not None and row['implementation'] not in implementation_ids:
            raise ValueError('Candidate implementation is outside verified sealed sources')
        row['implementation_id']=implementation_ids.get(row['implementation'])
        relation=projection['graph']['candidates'].get(row['id'],{})
        row.update(parent_candidate=relation.get('parent'),mechanism_ref=relation.get('mechanism_ref'),
                   derivation=relation.get('derivation','unknown'),support=relation.get('support','unknown'),
                   execution_status=relation.get('execution_status','unknown'))
    candidates=paged(candidate_rows,16,'candidates','inputs/history/candidates','observed-candidates')
    mechanisms=[dict(mechanism_ref=ref,label=_clip(item['label'],80),label_truncated=len(item['label'].encode())>80,
                     minimal_test=_clip(item['minimal_test'],160),minimal_test_truncated=len(item['minimal_test'].encode())>160,
                     status=item['status'],original=f'inputs/history/nodes/{item["source_h"]:03d}/0001.json')
                for ref,item in sorted(state['mechanisms'].items())] if state else []
    for row in mechanisms:
        disposition=state['mechanisms'][row['mechanism_ref']].get('disposition')
        if disposition is not None:
            reason=disposition['reason']
            original=f'inputs/history/nodes/{disposition["source_a"]:03d}/scientific-original.json'
            if not any(item['path']==original for item in files):
                source=next(source for source in projection['sources'] if source[0]['step']==disposition['source_a'])
                raw=c.regular(c._execution_raw(root,disposition['source_a']))
                destination=c.workspace(root,step)/original
                bind_original(raw,destination,source[2]['raw_ref'])
                files.append(dict(path=original,size_bytes=destination.stat().st_size,
                    kind='scientific_original'))
            row['disposition']=dict(reason=_clip(reason,160),reason_truncated=len(reason.encode())>160,
                original=original)
    if state and 'portfolio' in state:
        initial_calls=configuration_updates.for_new_job(root,step)['search_policy'].get('initial_calls',1)
        for row in mechanisms:
            ref=row['mechanism_ref']
            row['accepted_calls']=state['opportunity_counts'].get(ref,0)
            row['initial_calls']=initial_calls
            row['initial_calls_remaining']=max(0,initial_calls-row['accepted_calls'])
            row['execution_usage_unknown']=state['opportunity_unknown']
            grant=state.get('extra_grant')
            row['extra_calls_remaining']=(max(0,1-sum(count for (source_a,_),count in state['opportunity_grants'].items() if source_a==grant['source_a']))
                if grant and grant['mechanism_ref']==ref else 0)
            row['completed_cycles'] = state['portfolio']['invested'].get(row['mechanism_ref'], 0)
            row['unserved_cycles'] = state['portfolio']['deferred'].get(row['mechanism_ref'], 0)
            if state and 'portfolio' in state:
                pending = state['portfolio']['pending_w'] if role == 'A' else None
                row['unserved_cycles_if_assessed'] = (row['unserved_cycles'] + 1
                    if pending and row['mechanism_ref'] not in (pending.get('branches',[pending['branch']])) and row['status']!='waived'
                    else 0 if pending and row['mechanism_ref'] in (pending.get('branches',[pending['branch']])) else row['unserved_cycles'])
    mechanism_pages=paged(mechanisms,16,'mechanisms','inputs/lineage/mechanisms','sealed-H-A-interpretation')
    order_rows = [dict(mechanism_ref=item['mechanism_ref'], label=_clip(state['mechanisms'][item['mechanism_ref']]['label'], 80),
                       original=f'inputs/history/nodes/{item["planned_step"]:03d}/0001.json')
                  for item in state['order']['remaining']] if state else []
    order_pages = paged(order_rows, 16, 'opportunities', 'inputs/lineage/order', 'sealed-scientific-exploration-order') if state else []
    relations=paged(public_relations(projection['graph'],projection['sources']),16,'relations','inputs/lineage/relations','declared-not-proven-causality')
    indexes=dict(nodes=nodes[0] if nodes else None,candidates=candidates[0] if candidates else None,
                 mechanisms=mechanism_pages[0] if mechanism_pages else None,relations=relations[0] if relations else None)
    experiments=[];recovery_materials=[]
    source_jobs={job['step']:job for job,_,_,_ in projection['sources']}
    for record in records:
        owner=originals.node(root,record['step'])
        def archive_path(proof):
            return originals.resolve_ref(originals.source_for_ref(owner,proof),dict(proof,path=proof['snapshot']))
        resumes={row['call_id']:row for row in record.get('execution_resumes',[])}
        for resumed in resumes.values():
            if resumed['phase'] in {'resume_intent','original_prepare_unknown'}:
                experiments.append(dict(step=record['step'],call_id=resumed['call_id'],execution_status='unknown',
                    limitation='Original preparation may have started; no accepted execution or scientific result is claimed. Preserve the original identity.'))
        for call in (record.get('observation') or {}).get('managed_calls',[]):
            if call.get('retirement') is not None:
                original=c.read(archive_path(call['retirement']))
                prefix=f'inputs/recovered/{record["step"]:03d}/{call["call_id"]}'
                retained=[(call['retirement'],'retirement.json')]
                native=[original[key] for key in ('request','response','protocol')]
                if original.get('dispatch_intent') is not None:native.append(original['dispatch_intent'])
                for number,proof in enumerate(native,1):
                    matches=[item for item in call['originals'] if Path(owner.original_root)/item['path']==Path(proof['path'])
                             and item==proof and item['size_bytes']==proof['size_bytes']]
                    if len(matches)!=1:raise ValueError('Retirement native original is unavailable')
                    retained.append((matches[0],f'originals/{number:03d}'))
                for item,name in retained:
                    if worker_view_version == 1:
                        continue  # Exact receipts remain in the private verified archive.
                    limits=configuration_updates.for_new_job(root,step)
                    if c.output_quota_exceeded(limits,(row['size_bytes'] for row in [*recovery_materials,item])):
                        raise ValueError('Retirement proof materials exceed the original input bound')
                    target=f'{prefix}/{name}'
                    bind_original(archive_path(item),c.workspace(root,step)/target,item)
                    recovery_materials.append(dict(path=target,size_bytes=item['size_bytes']))
                experiments.append(dict(step=record['step'],call_id=call['call_id'],execution_status='not_submitted',
                    **(dict(retirement_proof=f'{prefix}/retirement.json',native_originals=[f'{prefix}/{name}' for _,name in retained[1:]])
                       if worker_view_version != 1 else {}),
                    limitation='Permanent non-submission of the original call only; no scientific verdict, candidate or new execution allowance.'))
                continue
            if 'execution' not in call:
                experiments.append(dict(step=record['step'],call_id=call['call_id'],execution_status='unknown',
                    native_state=(resumes.get(call['call_id']) or {}).get('native_state'),
                    limitation='Original accepted execution is pending or unverified; preserve its identity and reservation.'))
                continue
            details=call.get('execution') or {};result=details.get('result') or {}
            scientific_outputs=(history.scientific_outputs(root,source_jobs[record['step']],call)
                if worker_view_version==1 else call.get('outputs',[]))
            outputs=[dict(path=f'inputs/parents/{record["step"]:03d}/artifacts/experiments/{call["call_id"]}/artifacts/{item["name"]}',
                          historical=f'history/nodes/{record["step"]:03d}/artifacts/experiments/{call["call_id"]}/artifacts/{item["name"]}',
                          retention=item['retention']) for item in scientific_outputs]
            if call['call_id'] in record.get('recovered_calls',[]):
                for output,item in zip(outputs,scientific_outputs):
                    output['path']=f'inputs/recovered/{record["step"]:03d}/{call["call_id"]}/{item["name"]}'
                    output['historical']=output['path']
                    limits=configuration_updates.for_new_job(root,step)
                    if c.output_quota_exceeded(limits,(row['size_bytes'] for row in [*recovery_materials,item])):
                        raise ValueError('Execution recovery materials exceed the original input bound')
                    bind_original(archive_path(item),c.workspace(root,step)/output['path'],item)
                    recovery_materials.append(dict(path=output['path'],size_bytes=item['size_bytes']))
            native_originals=[]
            if call['call_id'] in record.get('recovered_calls',[]):
                observed=c.read(archive_path(call['observation']))
                for number,proof in enumerate(observed['proofs'],1):
                    matches=[item for item in call['originals'] if Path(owner.original_root)/item['path']==Path(proof['path']) and item==proof]
                    if len(matches)!=1 or matches[0]['retention']!='archived':raise ValueError('Recovered native original is unavailable')
                    if worker_view_version == 1:
                        continue  # Do not publish host control proofs as scientific inputs.
                    item=matches[0];target=f'inputs/recovered/{record["step"]:03d}/{call["call_id"]}/originals/{number:03d}'
                    limits=configuration_updates.for_new_job(root,step)
                    if c.output_quota_exceeded(limits,(row['size_bytes'] for row in [*recovery_materials,item])):
                        raise ValueError('Execution recovery originals exceed the original input bound')
                    bind_original(archive_path(item),c.workspace(root,step)/target,item)
                    recovery_materials.append(dict(path=target,size_bytes=item['size_bytes']))
                    native_originals.append(dict(path=target))
            native_pages=paged(native_originals,16,'originals',f'inputs/history/recoveries/{record["step"]:03d}/{call["call_id"]}','recovered-native-originals') if native_originals else []
            metrics=result.get('metrics',[])
            experiments.append(dict(step=record['step'],call_id=call['call_id'],execution_status=call['status'],
                returncode=details.get('returncode'),wall_seconds=details.get('wall_seconds'),
                **(dict(native_state=details['native_state']) if 'native_state' in details else {}),
                result_status=result.get('status','unobserved'),diagnostic=result.get('diagnostic'),
                **(dict(public_result=details['public_result']) if 'public_result' in details else {}),
                metrics=metrics[:8],metrics_omitted_count=max(0,len(metrics)-8),
                outputs=outputs[:8],outputs_omitted_count=max(0,len(outputs)-8),
                **(dict(native_originals_index=native_pages[0],recovery_note='Appended original terminal facts; the prior sealed scientific conclusion is unchanged.') if native_pages else {}),
                **(dict(recovery_note='Retained terminal observations; the prior sealed scientific conclusion is unchanged. Exact control receipts remain private.')
                   if worker_view_version == 1 and call['call_id'] in record.get('recovered_calls',[]) else {}),
                limitation='Declared metrics and retained files; candidate ranking, scientific validity and external submission are not established. Direct-parent paths are mounted only for bound parents; use historical handles for other nodes.'))
    experiment_pages=paged(experiments,2,'experiments','inputs/history/experiments','managed-local-observations')
    if experiment_pages:indexes['experiments']=experiment_pages[0]
    audit_refs, audit_rows = [], []
    if audit_projection_version == 1:
        from . import candidate_audit
        audit_refs = candidate_audit.collect_refs(root, projection['sources'], step=step, adjacent=adjacent_audit)
        audit_rows = candidate_audit.project_receipts(root, records, audit_refs)
        for index, row in enumerate(audit_rows):
            row['original'] = f'inputs/lineage/audits/{index // 16 + 1:04d}.json'
        audit_pages = paged(audit_rows, 16, 'adapter_audits', 'inputs/lineage/audits', 'frozen-adapter-audit-receipts')
        indexes['audits'] = audit_pages[0] if audit_pages else None
    elif audit_projection_version != 0:
        raise ValueError('Unsupported frozen audit projection')
    view=brief(records,state,projection['graph'],overview,indexes,selection_protocol=selection_protocol)
    if state and 'portfolio' in state:
        threshold = (configuration_updates.for_new_job(root,step).get('portfolio_policy') or {}).get('review_after_cycles')
        # Stable identity order is navigation, never scientific ranking.
        view['mechanisms'].update(overview=mechanisms[:4], overview_omitted_count=max(0,len(mechanisms)-4),
                                  index=indexes['mechanisms'],
                                  portfolio=search.portfolio_view(state,threshold,prospective=role=='A'))
    if state:
        view['mechanisms']['unfinished_plan']=dict(remaining=order_rows[:4],remaining_count=len(order_rows),
            omitted_count=max(0,len(order_rows)-4),index=order_pages[0] if order_pages else None,
            rule='Original unfinished opportunities before this A assessment. Null follows the plan; explicit scientific selection does not reorder or discard omitted items. Only an eligible final W assessment consumes its allocation.')
    bounded_brief(view)
    if audit_projection_version == 1:
        candidates_view = view['candidates']
        if selection_protocol == 1:
            explicit = [row for row in candidates_view['public_audits'] if row['status'] != 'unknown']
            if explicit:
                candidates_view['public_audits'] = explicit
            else:
                candidates_view.pop('public_audits')
                candidates_view.pop('audits_omitted_count')
        candidates_view.update(adapter_audits=audit_rows[:2], adapter_audits_omitted_count=max(0, len(audit_rows)-2),
            leakage_assessment='Unknown unless explicitly reported; adapter status does not establish leakage or target suitability.')
        bounded_brief(view)
    nomination_ids=None
    if state is not None and configuration_updates.for_new_job(root,step).get('candidate_nomination_policy') is not None:
        from . import candidate_nominations
        nominated=candidate_nominations.project(records,state)
        nomination_pages=paged(candidate_nominations.page_rows(nominated),16,'nominations',
            'inputs/history/nominations','sealed-W-scientific-nominations')
        nomination_index=nomination_pages[0] if nomination_pages else None
        indexes['nominations']=nomination_index
        view['candidates']['nominations']=candidate_nominations.preview(nominated,nomination_index)
        nomination_ids=list(dict.fromkeys(row['candidate'] for row in [*nominated['representatives'],*nominated['extras']]))
        # The default comparison preview is W's declaration, not the first pool
        # members in observation order. Every pool member stays in the index.
        view['candidates']['candidate_facts']=[]
        view['candidates']['candidate_facts_omitted_count']=len(candidate_rows)
        bounded_brief(view)
    return dict(**(dict(execution_recoveries=projection['recovery_refs'],execution_recovery_materials=recovery_materials) if projection['recovery_refs'] else {}),**(dict(audit_projection_version=1, audit_receipts=audit_refs, adjacent_audit=adjacent_audit) if audit_projection_version == 1 else {}), nodes=[dict(step=row['step'],seal_ref=row['seal_ref']) for row in records],files=files,
                candidate_ids=[row['id'] for row in candidate_rows],
                **(dict(candidate_nomination_ids=nomination_ids) if nomination_ids is not None else {}),
                **(dict(candidate_references=references) if worker_view_version==1 else {}),
                view=bounded_brief(candidate_refs.public_fields(view,references))),state
