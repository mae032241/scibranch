"""Explicit local result declarations projected into existing candidate bindings."""
import hashlib
import math
from pathlib import Path

from . import contracts as c
from . import experiments as e


def identity(row, candidate):
    return 'local-' + hashlib.sha256(e.encoded(dict(scope=row['scope'], call_id=row['call_id'],
        artifact=candidate['artifact']))).hexdigest()


def protocol(interface):
    if 'result_protocol' in interface:return interface['result_protocol']
    # Read-only compatibility for the exact previously published local binding.
    if interface.get('executor')=='local_execution:command' and type(interface.get('version')) is int and interface['version']==1:
        return e.LOCAL_RESULT_PROTOCOL
    return None


def declarations(row):
    result=row.get('execution',{}).get('result') or {}
    members=result.get('candidates',[]) if isinstance(result,dict) else []
    return members if isinstance(members,list) else []


def candidates(rows, *, diagnostics=None):
    result = []
    for row in rows:
        if row.get('result_protocol')!=e.LOCAL_RESULT_PROTOCOL:continue
        declared = row.get('execution', {}).get('result') or {}
        if not isinstance(declared,dict):continue
        declarations = declared.get('candidates', [])
        if row['status'] != 'known_success' or declared.get('status') != 'available':continue
        if not isinstance(declarations,list):
            if diagnostics is not None:diagnostics.append(dict(call_id=row['call_id'],code='declared_candidate_mapping_invalid'))
            continue
        for candidate in declarations:
            metrics=declared.get('metrics');outputs=row.get('outputs')
            valid=(isinstance(candidate,dict) and isinstance(candidate.get('artifact'),str)
                and isinstance(metrics,list) and isinstance(outputs,list))
            matching_outputs=[item for item in outputs if isinstance(item,dict) and isinstance(item.get('name'),str)
                and 'artifacts/'+item['name']==candidate['artifact']] if valid else []
            declared_outputs=declared.get('outputs')
            valid=(valid and isinstance(declared_outputs,list) and len(matching_outputs)==1
                and sum(isinstance(item,dict) and item.get('path')==candidate['artifact'] for item in declared_outputs)==1
                and sum(isinstance(item,dict) and item.get('artifact')==candidate['artifact'] for item in declarations)==1)
            if not valid:
                if diagnostics is not None:diagnostics.append(dict(call_id=row['call_id'],code='declared_candidate_mapping_invalid'))
                continue
            matching_metrics=[item for item in metrics if isinstance(item,dict)
                and isinstance(candidate.get('metric'),str) and item.get('name')==candidate['metric']]
            metric=matching_metrics[0] if len(matching_metrics)==1 else {}
            value=metric.get('value')
            try:finite=type(value) in (int,float) and math.isfinite(value)
            except OverflowError:finite=False
            if not finite:
                metric={};value=None
                if 'metric' in candidate and diagnostics is not None:
                    diagnostics.append(dict(call_id=row['call_id'],code='declared_candidate_metric_unknown'))
            artifact=matching_outputs[0]
            result.append(dict(id=identity(row, candidate), label=candidate.get('label'), implementation=None,
                evaluation_key=metric.get('evaluation_key'), metric=value, direction=metric.get('direction'), status='ok',
                evidence=[row['completed'], artifact]))
    return result


def sources(root, fact, candidate_id, *, source=None):
    """Resolve only a declared local candidate, from its exact sealed call archive."""
    from . import originals, candidate_refs
    if source is None:
        source = originals.node(root, fact['managed_calls'][0]['scope']['step']) if fact.get('managed_calls') else None
    raw_id = candidate_refs.original_id(source.origin_identity, candidate_id) if source is not None else candidate_id
    def retained(proof):
        bound=dict(path=proof['snapshot'],sha256=proof['sha256'],size_bytes=proof['size_bytes'])
        return originals.resolve_ref(source,bound)
    matches = [(row, item) for row in fact.get('managed_calls', [])
        for item in declarations(row)
        if isinstance(item,dict) and isinstance(item.get('artifact'),str) and identity(row,item)==raw_id]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError('Managed candidate identity is ambiguous')
    row, declaration = matches[0]
    if 'result_protocol' not in row:
        from . import history
        accepted=c.read(retained(row['accepted']))
        row=dict(row,result_protocol=protocol(history._execution_interface(root,accepted)))
    observed = [item for item in candidates([row]) if item['id'] == raw_id]
    if len(observed) != 1 or not any(dict(item,id=raw_id)==observed[0] for item in fact['candidates'] if item['id']==candidate_id):
        raise ValueError('Managed candidate differs from declared sealed result')
    accepted = c.read(retained(row['accepted']))
    completed = c.read(retained(row['completed']))
    if 'entry' not in accepted:
        raise ValueError('Managed delivery requires retained experiment inputs')
    def proof(item):
        if item.get('retention') != 'archived':
            raise ValueError('Managed delivery original is not archived')
        return dict(path=item['snapshot'], sha256=item['sha256'], size_bytes=item['size_bytes'])
    # Input names come from the accepted snapshot inventory, never basename matching.
    objects = {}
    for item in accepted['snapshot_files']:
        matches = [p for p in row['originals'] if Path(source.original_root) / p['path'] == Path(accepted['cwd']) / item['name']]
        if len(matches) != 1 or any(matches[0][key] != item[key] for key in ('sha256', 'size_bytes')):
            raise ValueError('Managed delivery input differs from accepted snapshot')
        objects['inputs/' + item['name']] = proof(matches[0])
    for item in row['outputs']:
        objects['outputs/' + item['name']] = proof(item)
    completed_declarations=completed['result']['candidates']
    if sum(item==declaration for item in completed_declarations)!=1:
        raise ValueError('Managed candidate differs from completed result')
    objects['artifact'] = objects['outputs/' + declaration['artifact'].removeprefix('artifacts/')]
    return dict(candidate_id=candidate_id, status='audit_pending',
        ledger=proof(row['completed']), objects=objects,
        context=dict(kind='managed_local_result', call_id=row['call_id'],
            replay=dict(entry=accepted['entry'], arguments=accepted['command'][accepted['script_index'] + 1:]),
            candidate=declaration, metrics=completed['result']['metrics'],
            limitation='Declared public measurements; scientific validity and target optimality are not qualified.'))
