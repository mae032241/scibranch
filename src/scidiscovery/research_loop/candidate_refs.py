"""Scientific candidate references bound privately to unchanged canonical IDs.

Only declared candidate identity slots in framework-produced views are projected.
Scientific prose, metric names, source files and arbitrary user JSON are untouched.
"""
import copy
import re

_MACHINE_CANDIDATE=re.compile(r'local-[0-9a-f]{64}\Z')
_REFERENCE=re.compile(r'[HWAE]\d{3,}-C\d{3,}(?:/artifacts/.+)?\Z')
_ORIGIN_CANDIDATE=re.compile(r'source:([0-9a-f]{64}):(.+)\Z', re.DOTALL)
_SLOTS={'id','candidate_id','candidate_ids','parent_candidate','original_candidate',
    'recommendation','candidate_recommendation','subject'}


def valid_parent_id(value):
    """Bound typed host keys separately; callers still bind the exact producer."""
    if not isinstance(value,str):return False
    if 0<len(value.encode())<=80:return True
    match=_ORIGIN_CANDIDATE.fullmatch(value)
    return match is not None and 0<len(match[2].encode())<=80


def origin_candidate(identity, raw_id):
    """A private control key; the retained scientific identifier stays intact."""
    if not isinstance(raw_id, str) or _MACHINE_CANDIDATE.fullmatch(raw_id):
        return raw_id
    if _ORIGIN_CANDIDATE.fullmatch(raw_id):
        return raw_id  # Only framework input views use this reserved ':' form.
    from . import originals
    return 'source:' + originals.identity_key(identity) + ':' + raw_id


def original_id(identity, candidate):
    from . import originals
    match = _ORIGIN_CANDIDATE.fullmatch(candidate) if isinstance(candidate, str) else None
    return match[2] if match is not None and match[1] == originals.identity_key(identity) else candidate


def in_instance(root, step, raw_id):
    from . import originals
    return origin_candidate(originals.node(root, step).origin_identity, raw_id) if originals.inherited(root, step) else raw_id


def source_input_mapping(source, job, *, _active=None):
    """Resolve input IDs by exact observed producers, never equal raw labels."""
    from . import originals, contracts as c
    active = set() if _active is None else _active
    key = (originals.identity_key(source.origin_identity), source.step)
    if key in active or len(active) >= 16:
        raise ValueError('Candidate source input ancestry is cyclic or exceeds its bound')
    if job.get('fork_source') is not None:
        original = originals.source_for_ref(source, job['fork_source']['job'])
        originals.resolve_ref(original, job['fork_source']['job'])
        return source_input_mapping(original, originals.source_job(original), _active={*active, key})
    known = job.get('history', {}).get('candidate_ids', [])
    result = {item: item for item in known if _MACHINE_CANDIDATE.fullmatch(item) or _ORIGIN_CANDIDATE.fullmatch(item)}
    owners = {}
    for row in job.get('history', {}).get('nodes', []):
        producer = originals.dependency(source, row['step'], seal_sha256=row['seal_sha256'])
        seal = c.read(originals.source_record(producer, 'result-identity.json'))
        receipt = seal.get('observations')
        if receipt is None:
            continue
        observation = c.read(originals.resolve_ref(producer, receipt))
        for item in observation['candidates']:
            raw = item['id']
            if raw in known and raw not in result:
                owners.setdefault(raw, []).append(producer.origin_identity)
    for raw, identities in owners.items():
        own = [identity for identity in identities if identity == source.origin_identity]
        identities = own or identities
        keys = {origin_candidate(identity, raw) for identity in identities}
        if len(keys) != 1:
            raise ValueError('Candidate input has ambiguous original instance identity')
        result[raw] = keys.pop()
    return result


def source_observation(source, job, value):
    """Typed host projection of inherited observations, separate from originals."""
    inputs = source_input_mapping(source, job)
    own = {row['id']: origin_candidate(source.origin_identity, row['id']) for row in value['candidates']}
    result = copy.deepcopy(value)
    def bind(proof):
        if isinstance(proof, dict) and proof.get('retention') == 'archived':
            proof.setdefault('origin_identity', source.origin_identity)
    for row in result['candidates']:
        row['id'] = own[row['id']]
        for proof in row.get('evidence', []):
            bind(proof)
    for field in ('current_candidate', 'actual_submission'):
        row = result.get(field)
        if row is not None and row.get('candidate_id') is not None:
            identity = row['candidate_id']
            row['candidate_id'] = own.get(identity, inputs.get(identity, identity))
            for proof in row.get('evidence', []):
                bind(proof)
            if row.get('artifact') is not None:
                bind(row['artifact'])
    for row in result.get('contexts', []):
        identity = row['candidate_id']
        row['candidate_id'] = own.get(identity, inputs.get(identity, identity))
        parent = row.get('parent')
        if parent is not None and parent.get('candidate_id') is not None:
            identity = parent['candidate_id']
            parent['candidate_id'] = (own.get(identity, origin_candidate(source.origin_identity, identity))
                if parent.get('source') == 'same_node_completed_call' else inputs.get(identity, identity))
        for proof in row.get('proofs', []):
            bind(proof)
        from . import lineage
        for artifact in lineage.context_artifacts(row):bind(artifact)
    for call in result.get('managed_calls', []):
        for field in ('accepted', 'completed', 'terminal', 'observation', 'retirement'):
            bind(call.get(field))
        for field in ('originals', 'outputs'):
            for proof in call.get(field, []):
                bind(proof)
    return result


def source_job_view(source, job):
    result = copy.deepcopy(job)
    mapping = source_input_mapping(source, job)
    history = result.get('history', {})
    history['candidate_ids'] = [mapping.get(item, item) for item in history.get('candidate_ids', [])]
    if 'candidate_references' in history:
        history['candidate_references'] = {mapping.get(key, key): value for key, value in history['candidate_references'].items()}
    return result


def _input_mapping(root, job, source=None):
    if job.get('fork_source') is None:
        return {}
    from . import originals
    if source is None:
        if root is None:
            return {}
        source = originals.selected_source(root)
    else:
        source = originals.source_for_ref(source, job['fork_source']['job'])
    return source_input_mapping(source, originals.source_job(source))


def available_ids(root, job, *, source=None):
    mapping = _input_mapping(root, job, source)
    return [mapping.get(item, item) for item in job.get('history', {}).get('candidate_ids', [])]


def references(records):
    """Assign from each immutable source call order, including unsuccessful calls."""
    result={};owners={}
    for record in records:
        observed=record.get('observation') or {}
        calls={row['call_id']:number for number,row in enumerate(observed.get('managed_calls',[]),1)}
        from . import generic_results
        canonical={generic_results.identity(row,item):(calls[row['call_id']],item['artifact'])
            for row in observed.get('managed_calls',[])
            for item in generic_results.declarations(row)
            if isinstance(item,dict) and isinstance(item.get('artifact'),str)}
        for candidate in observed.get('candidates',[]):
            identity=candidate['id']
            if _ORIGIN_CANDIDATE.fullmatch(identity):
                if identity not in result:
                    result[identity] = f'{record["role"]}{record["step"]:03d}-C{observed["candidates"].index(candidate)+1:03d}'
                continue
            if not _MACHINE_CANDIDATE.fullmatch(identity):continue
            if identity in result:continue  # Re-observation retains its original reference.
            if identity not in canonical:raise ValueError('Candidate reference lacks its exact original call')
            call_number,artifact=canonical[identity]
            ref=f'{record["role"]}{record["step"]:03d}-C{call_number:03d}/{artifact}'
            if identity in result and result[identity]!=ref or ref in owners and owners[ref]!=identity:
                raise ValueError('Candidate reference is ambiguous')
            result[identity]=ref;owners[ref]=identity
    return result


def for_job(job):
    return (job.get('history') or {}).get('candidate_references',{}) if job.get('worker_view_version')==1 else {}


def public_reference(root, job, identity):
    mapping=for_job(job)
    if identity in mapping:return mapping[identity]
    if job.get('worker_view_version')==1 and isinstance(identity,str) and _MACHINE_CANDIDATE.fullmatch(identity):
        raise ValueError('Candidate has no frozen scientific reference')
    return identity


def public_fields(value, mapping):
    """Project candidate slots in a typed framework view, never arbitrary science."""
    if isinstance(value,list):return [public_fields(item,mapping) for item in value]
    if not isinstance(value,dict):return value
    result={}
    for key,item in value.items():
        if key in _SLOTS and isinstance(item,str):result[key]=mapping.get(item,item)
        elif key=='candidate_ids' and isinstance(item,list):result[key]=[mapping.get(identity,identity) if isinstance(identity,str) else identity for identity in item]
        elif key in {'metrics','scientific_result','text','reported','result'}:result[key]=item
        else:result[key]=public_fields(item,mapping)
    return result


def public_science_fields(value, mapping):
    """Project only declared choice slots in an authored scientific response."""
    result=dict(value)
    chosen=result.get('candidate_recommendation')
    if isinstance(chosen,str):result['candidate_recommendation']=mapping.get(chosen,chosen)
    nominations=result.get('candidate_nominations')
    if isinstance(nominations,dict):
        nominations=copy.deepcopy(nominations)
        for field in ('representatives','extras'):
            for row in nominations.get(field,[]) if isinstance(nominations.get(field),list) else []:
                if isinstance(row,dict) and isinstance(row.get('candidate'),str):
                    row['candidate']=mapping.get(row['candidate'],row['candidate'])
        result['candidate_nominations']=nominations
    selected=result.get('implementation_selection')
    if isinstance(selected,list):
        result['implementation_selection']=[mapping.get(item,item) if isinstance(item,str) else item for item in selected]
    table=result.get('candidate_comparison')
    if isinstance(table,dict) and isinstance(table.get('rows'),list):
        rows=[]
        for row in table['rows']:
            if isinstance(row,dict) and isinstance(row.get('candidate_id'),str):
                row=dict(row,candidate_id=mapping.get(row['candidate_id'],row['candidate_id']))
            rows.append(row)
        result['candidate_comparison']=dict(table,rows=rows)
    return result


def resolve_reference(root, job, identity, *, source=None):
    if identity is not None and not isinstance(identity,str):
        raise ValueError('Candidate reference must name one exact scientific object')
    if job.get('worker_view_version')!=1:return identity
    mapping=for_job(job);inverse={ref:canonical for canonical,ref in mapping.items()}
    if len(inverse)!=len(mapping):raise ValueError('Candidate reference is ambiguous')
    inputs = _input_mapping(root, job, source)
    if identity in inverse:return inputs.get(inverse[identity], inverse[identity])
    if identity in inputs:return inputs[identity]
    if isinstance(identity,str) and _REFERENCE.fullmatch(identity):
        raise ValueError('Candidate reference is outside this frozen scientific task')
    if identity in mapping:raise ValueError('Use the listed scientific candidate reference')
    return identity


def control_payload(job, payload, *, root=None, source=None):
    """Resolve only structured author choices; preserve the original return bytes."""
    if job.get('worker_view_version')!=1:return payload
    def resolve(identity):
        try:return resolve_reference(root,job,identity,source=source)
        except ValueError:return identity  # Unused scientific advice cannot veto its original.
    value=copy.deepcopy(payload)
    if job['role']=='W' and 'candidate_nominations' in value:
        from . import candidate_nominations
        value['candidate_nominations']=candidate_nominations.resolve(root,job,value['candidate_nominations'])
    if 'candidate_recommendation' in value:value['candidate_recommendation']=resolve(value['candidate_recommendation'])
    if root is not None and isinstance(value.get('candidate_recommendation'),str) and re.fullmatch(r'[HWAE]\d{3,}-I\d{3,}',value['candidate_recommendation']):
        from . import history
        matches={row['candidate_id'] for row in history.candidate_materials(root,job)
            if row['implementation_id']==value['candidate_recommendation']}
        if len(matches)==1:value['candidate_recommendation']=matches.pop()
    return value
