"""Bounded delivery bindings and isolated calls; the control ledger owns actions."""
from __future__ import annotations

from . import configuration_updates
from . import execution

from scidiscovery import execution_security as security
import copy
import json
import math
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import tempfile
import time

from . import budget, contracts as c, history, originals, runtime
from .instance_owner import effect as owner_effect

class _Deadline:
    def __init__(self, end):
        self.end = end

    def remaining_seconds(self):
        remaining = self.end - time.clock_gettime(time.CLOCK_BOOTTIME)
        if remaining <= 0:
            raise TimeoutError('Original delivery preparation deadline reached')
        return remaining


def _stage(root, prefix):
    directory = Path(root) / 'host-delivery'
    if directory.resolve() != directory:
        raise ValueError('Delivery staging namespace changed')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=directory)


SOURCE_SECONDS = 5  # Mechanical transport safety, never a scientific cost estimate.
MAX_LEAF_TAIL = 64 * 1024  # Host-only stream tail retained per isolated leaf call.
CAPABILITIES = {'smoke', 'audit'}  # Explicit optional adapter phases; legacy adapters require audit.


class DeliveryUnresolved(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class PreparationUnavailable(DeliveryUnresolved):
    """Retained author decision needs retryable transport, not author correction."""


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True) + '\n').encode()


def _file(path, limit=None):
    path = Path(path).absolute()
    if path.resolve() != path:
        raise ValueError('Delivery requires an exact original, without symbolic links')
    return c.regular(path, limit=limit)


def policy(value):
    keys = {'closeout_a', 'inflight_tokens', 'cleanup_seconds', 'submit_seconds', 'confirm_seconds', 'adapter'}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('Delivery requires explicit closeout, execution and transport ceilings')
    a = value['closeout_a']
    budget.node_policy({role: a for role in ('H', 'W', 'A', 'E')})
    if type(value['inflight_tokens']) is not int or value['inflight_tokens'] < 0:
        raise ValueError('In-flight allowance must be a nonnegative integer')
    for key in ('cleanup_seconds', 'submit_seconds', 'confirm_seconds'):
        amount = value[key]
        if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
            raise ValueError('Explicit finite delivery seconds required')
    adapter = value['adapter']
    if adapter is None:
        if value['submit_seconds'] != 0 or value['confirm_seconds'] != 0:
            raise ValueError('Export-only delivery has no external execution allowance')
        return {**value, 'closeout_a': dict(a)}
    if value['submit_seconds'] <= 0 or value['confirm_seconds'] <= 0:
        raise ValueError('An adapter requires positive submit and confirm ceilings')
    if not isinstance(adapter, dict) or set(adapter)-{'capabilities', 'effect_scope', 'readonly_inputs_version'} != {'adapter', 'launcher_argv', 'config', 'target', 'inputs', 'source_roots'}:
        raise ValueError('Adapter requires exact code, launcher, scoped inputs, configuration and target')
    if type(adapter.get('readonly_inputs_version')) is not int or adapter['readonly_inputs_version'] != 1:
        raise ValueError('Delivery provider must declare readonly_inputs_version 1')
    if 'effect_scope' in adapter and (not isinstance(adapter['effect_scope'], str)
            or not adapter['effect_scope'].strip()):
        raise ValueError('Adapter effect_scope must be nonempty public text')
    if 'capabilities' in adapter and (not isinstance(adapter['capabilities'],list)
            or any(not isinstance(name,str) or name not in CAPABILITIES for name in adapter['capabilities'])
            or len(adapter['capabilities']) != len(set(adapter['capabilities']))):
        raise ValueError('Adapter capabilities must explicitly name supported optional phases')
    script = _file(adapter['adapter'])
    argv = adapter['launcher_argv']
    if not isinstance(argv, list) or not argv or any(not isinstance(s, str) or not s for s in argv):
        raise ValueError('An explicit isolating launcher argv is required')
    launcher_files = []
    for arg in argv:
        path = Path(arg)
        if path.is_file():
            path = path.resolve()
            launcher_files.append(dict(path=str(_file(path)), invoked_path=str(Path(arg).absolute())))
    if not launcher_files or not Path(argv[0]).is_absolute():
        raise ValueError('Launcher executable and original files must be frozen')
    if not isinstance(adapter['config'], dict) or not isinstance(adapter['target'], dict):
        raise ValueError('Adapter configuration and target must be bounded objects')
    inputs = adapter['inputs']
    roots = adapter['source_roots']
    if not isinstance(inputs,list) or not isinstance(roots,list) or not roots:
        raise ValueError('Explicit bounded provider input and read scope required')
    frozen_inputs, labels = [], set()
    for item in inputs:
        if not isinstance(item, dict) or set(item) != {'label', 'path'}:
            raise ValueError('Provider inputs require label and exact original')
        label = item['label']
        _label(label)
        if label in labels:raise ValueError('Duplicate provider input label')
        labels.add(label)
        invoked = Path(item['path']).absolute()
        path = _file(invoked.resolve())
        proof=dict(label=label,path=str(path),invoked_path=str(invoked))
        if label!='runtime-python':proof.update(size_bytes=path.stat().st_size)
        frozen_inputs.append(proof)
    for path in roots:
        if not isinstance(path, str) or not Path(path).is_absolute() or Path(path).resolve() != Path(path):
            raise ValueError('Provider read scopes require exact absolute originals')
    frozen = dict(adapter=str(script), launcher_argv=list(argv),
                  launcher_files=launcher_files, config=adapter['config'], target=adapter['target'],
                  inputs=frozen_inputs, source_roots=list(roots), readonly_inputs_version=1)
    if 'capabilities' in adapter:frozen['capabilities']=list(adapter['capabilities'])
    if 'effect_scope' in adapter:frozen['effect_scope']=adapter['effect_scope']
    return {**value, 'closeout_a': dict(a), 'adapter': frozen}


def public_configuration(value):
    """Describe configured effects without exposing private adapter bindings."""
    adapter = (value or {}).get('adapter')
    return dict(configured=adapter is not None,
        effect_scope=adapter.get('effect_scope', 'unknown') if adapter is not None else 'No delivery adapter is configured.')


def _label(label):
    if (not isinstance(label,str) or not label
            or Path(label).is_absolute() or '..' in Path(label).parts
            or '\\' in label or Path(label).as_posix() != label):
        raise ValueError('Canonical bounded provider object label required')


def _settings(root, job=None):
    return configuration_updates.for_job(root, job) if job is not None else configuration_updates.current(root)


def _adapter(root, job=None, *, context=None):
    spec = (_settings(root, job).get('delivery_policy') or {}).get('adapter')
    if spec is None:
        raise DeliveryUnresolved('delivery_adapter_unavailable')
    if spec.get('readonly_inputs_version') != 1:
        raise PreparationUnavailable('delivery_readonly_inputs_unsupported')
    for proof in spec['launcher_files']:
        if 'invoked_path' in proof and Path(proof['invoked_path']).resolve() != Path(proof['path']):
            raise ValueError('Frozen executable path now names another original')
        _file(proof['path'])
    _file(spec['adapter'])
    from . import storage_policy as storage
    inventory = storage.Inventory(_settings(root, job).get('storage_policy'), context=context)
    for proof in spec['inputs']:
        if 'invoked_path' in proof and Path(proof['invoked_path']).resolve() != Path(proof['path']):
            raise ValueError('Frozen executable or input path now names another original')
        if proof['label']=='runtime-python':
            _file(proof['path'])
            continue
        inventory.entry(); inventory.file(_file(proof['path']).stat().st_size)
        from . import experiments
        current = experiments.proof(_file(proof['path']), context=context)
        if any(current.get(key) != proof[key] for key in ('path','size_bytes','file_stat') if key in proof):
            raise ValueError('Frozen delivery executable or public harness changed')
    from . import generic_delivery
    if Path(spec['adapter']) == Path(generic_delivery.__file__).resolve():
        target = Path(spec['target']['directory'])
        if Path(root).resolve().is_relative_to(target):
            raise ValueError('Directory delivery target must not expose the control root')
    from . import protocol
    source=Path(protocol.__file__).resolve()
    if any(item['label']=='scid-current-protocol' for item in spec['inputs']):raise ValueError('Reserved current protocol input label')
    from . import experiments
    original=experiments.proof(source, context=context)
    item=dict(label='scid-current-protocol', **original)
    return dict(spec,inputs=[*spec['inputs'],item])


@owner_effect
def catalogue_sources(root, source_nodes, *, job=None):
    """Discover every exact candidate without opening executable/model originals.

    The complete bounded directory is readable in the A workspace. A resolver is
    called only for a selected ID, so historical objects cannot crowd it out.
    """
    return _catalogue_sources(root,source_nodes,job=job)


def _catalogue_sources(root, source_nodes, *, job=None):
    from .protocol import require_current
    require_current(root)
    _adapter(root, job)
    items, known = [], set()
    for receipt in source_nodes:
        source_job, _, seal, seal_sha = history._source(root, receipt['step'])
        if seal_sha != receipt['seal_ref']:
            raise ValueError('Delivery source scientific seal changed')
        observation = seal.get('observations')
        if observation is None:
            continue
        fact = history.read_observation(root, source_job, seal)
        if fact['binding']['source_ref'] != {key:seal['raw_ref'][key] for key in ('path','size_bytes')}:
            raise ValueError('Delivery observer source binding changed')
        for candidate in fact['candidates']:
            if candidate['id'] in known:
                raise ValueError('Delivery candidate identity is ambiguous')
            known.add(candidate['id'])
            items.append(dict(candidate_id=candidate['id'], status='available', source_step=receipt['step']))
    result = dict(catalogue=True, items=items, omitted_count=0, source_nodes=list(source_nodes))
    return result


def rebind_sources(root, refs, *, job=None):
    """Rebuild a branch catalogue from exact source seals, without old effects."""
    return _catalogue_sources(root, refs['source_nodes'], job=job) if refs is not None else None


def selected_sources(root, refs, candidate_id, *, prepare=False, job=None):
    """Reuse one immutable resolution, or prepare exactly one candidate's originals."""
    if not refs.get('catalogue'):
        return refs
    if candidate_id not in {row['candidate_id'] for row in refs['items']}:
        raise DeliveryUnresolved('delivery_source_unavailable')
    key = f'{refs["items"].index(next(row for row in refs["items"] if row["candidate_id"] == candidate_id)):04d}-{(job or {}).get("step", 0):03d}-{((job or {}).get("configuration_revision") or {}).get("revision", 0):06d}'
    path = root / 'host-delivery/resolved' / (key + '.json')
    if not path.exists():
        if not prepare:
            raise DeliveryUnresolved('delivery_source_unprepared')
        # Candidate audit records its own intent before resolving these inputs.
        # Its admission already checked other execution; that unlaunched intent
        # must not block its own read-only preparation.
        if preparation_blocked(root) or budget.closeout_state(root)['unresolved_operations']:
            raise DeliveryUnresolved('delivery_original_execution_unresolved')
        try:
            value = freeze_sources(root, refs['source_nodes'], candidate_id=candidate_id, job=job)
        except DeliveryUnresolved as error:
            preparation_failure(root, error.code, refs['source_nodes'], candidate_id=candidate_id)
            raise
        c.write(path, value)
        path.chmod(0o400)
    value = c.read(c.regular(path, limit=None))
    if value['source_nodes'] != refs['source_nodes'] or any(row['candidate_id'] != candidate_id for row in value['items']):
        raise ValueError('Selected delivery originals differ from their catalogue')
    return validate_sources(root, value, job=job)


def preparation_failure(root, reason, source_nodes, *, candidate_id=None, decision_step=None):
    directory = root / 'host-delivery/preparation'
    number = len(list(directory.glob('*.json'))) + 1
    c.write(directory / f'{number:03d}.json', dict(reason=reason, source_nodes=source_nodes,
        candidate_id=candidate_id, cleanup_confirmed=reason != 'delivery_cleanup_unknown',**(dict(decision_step=decision_step) if decision_step is not None else {})))


def preparation_status(root):
    paths = sorted((root / 'host-delivery/preparation').glob('*.json'))
    if not paths:
        return None
    value = c.read(c.regular(paths[-1]))
    return dict(attempts=len(paths), last_failure=value['reason'], candidate_id=value['candidate_id'],
        cleanup_confirmed=value['cleanup_confirmed'])


def preparation_blocked(root):
    return any(not c.read(c.regular(path))['cleanup_confirmed']
        for path in (root / 'host-delivery/preparation').glob('*.json'))


@owner_effect
def freeze_sources(root, source_nodes, *, candidate_id=None, job=None):
    """Freeze readable objects before A; no model load, audit or qualification claim."""
    from .protocol import require_current
    require_current(root)
    root = Path(root).resolve()
    remaining = budget.snapshot(root)['seconds_remaining']
    allowance = min(SOURCE_SECONDS, _settings(root, job)['delivery_policy']['cleanup_seconds'])
    if budget.final_a_barrier(root) is None and (budget.closeout_state(root)['begin'] or {}).get('mode') != 'final':
        allowance = min(allowance, remaining)
    if allowance <= 0:
        raise DeliveryUnresolved('delivery_source_preparation_unavailable')
    deadline = time.clock_gettime(time.CLOCK_BOOTTIME) + allowance
    from . import shared_originals, storage_policy as storage, experiments
    io_context = _Deadline(deadline)
    spec = _adapter(root, job, context=io_context)
    limits = _settings(root, job).get('storage_policy')
    inventory = storage.Inventory(limits, context=io_context)
    cache = {}
    def freeze(path, *, source_kind='mutable', proof=None, admitted=False):
        io_context.remaining_seconds()
        path = _file(path)
        if not admitted:
            inventory.entry(); inventory.file(path.stat().st_size)
        original = experiments.proof(path, context=io_context)
        if proof is not None and any(original[key] != proof[key] for key in ('size_bytes','file_stat') if key in proof):
            raise ValueError('Delivery source changed before freezing')
        if path in cache:return cache[path]
        cache[path] = dict(path=str(path), size_bytes=original['size_bytes'])
        return cache[path]
    def freeze_bound(proof, *, source):
        inventory.entry(); inventory.file(proof['size_bytes'])
        owner=originals.source_for_ref(source,proof)
        return freeze(_proof_path(root, proof, source=owner, context=io_context), source_kind='managed_frozen',
            proof=proof, admitted=True)
    def original_reference(source, proof):
        owner=originals.source_for_ref(source,proof)
        return dict(path=str(_proof_path(root,dict(path=proof.get('snapshot',proof['path']),
            size_bytes=proof['size_bytes'],**{key:proof[key] for key in ('origin_identity','file_stat') if key in proof}),
            source=owner,context=io_context)),size_bytes=proof['size_bytes'])
    candidates, known, evidence, contexts, direct = [], set(), {}, {}, []
    evidence_origins = {}; evidence_labels = {}; execution_labels = {}
    candidate_owners={}
    managed = (_settings(root, job).get('history_observer') or {}).get('contract_version') == 2
    for receipt in source_nodes:
        source_job, _, seal, seal_sha = history._source(root, receipt['step'])
        if seal_sha != receipt['seal_ref']:
            raise ValueError('Delivery source scientific seal changed')
        observation = seal.get('observations')
        if observation is None:
            continue
        fact = history.read_observation(root, source_job, seal)
        if fact['binding']['source_ref'] != {key:seal['raw_ref'][key] for key in ('path','size_bytes')}:
            raise ValueError('Delivery observer source binding changed')
        source = originals.node(root, receipt['step'])
        for context in fact.get('contexts', []):
            context_candidate_id = context.get('candidate_id')
            if context_candidate_id is not None:
                contexts.setdefault(context_candidate_id, []).append((source, context))
        for candidate in fact['candidates']:
            if candidate_id is not None and candidate['id'] != candidate_id:
                continue
            if candidate['id'] in known:
                raise ValueError('Delivery candidate identity is ambiguous')
            known.add(candidate['id']);candidate_owners[candidate['id']]=source
            from . import generic_results
            resolved = generic_results.sources(root, fact, candidate['id'], source=source)
            if resolved is not None:
                call=resolved['context']['call_id']
                artifact=resolved['objects']['artifact']
                name=resolved['context']['candidate']['artifact'].removeprefix('artifacts/')
                origins=[proof for row in fact['managed_calls'] if row['call_id']==call
                    for proof in row.get('outputs',[]) if proof['name']==name
                    and proof['snapshot']==artifact['path'] and proof['size_bytes']==artifact['size_bytes']]
                if len(origins)!=1:raise ValueError('Managed artifact has no exact original call output')
                resolved['artifact_origin']=original_reference(source,origins[0])
                resolved['audit_required'] = 'audit' in spec.get('capabilities', ['audit'])
                resolved['ledger'] = freeze_bound(resolved['ledger'], source=source)
                resolved['objects'] = {label: freeze_bound(proof, source=source) for label, proof in resolved['objects'].items()}
                if resolved['context'].get('source_code_version') is not None:
                    saved_accepted=next(entry for entry in fact['managed_calls'] if entry['call_id']==call)['accepted']
                    resolved['context']['accepted']=freeze_bound(dict(path=saved_accepted['snapshot'],
                        size_bytes=saved_accepted['size_bytes'],
                        **{key:saved_accepted[key] for key in ('file_stat',) if key in saved_accepted}),source=source)
                for proof in spec['inputs']:
                    if proof['label']=='runtime-python':continue
                    if proof['label'] in resolved['objects']:raise ValueError('Provider input collides with candidate original')
                    frozen = freeze(proof['path'], proof=proof)
                    resolved['objects'][proof['label']] = frozen
                direct.append(resolved)
                continue
            proofs = []
            for proof in candidate['evidence']:
                if proof.get('retention') != 'archived':continue
                bound = dict(path=proof['snapshot'], size_bytes=proof['size_bytes'],
                    **{key:proof[key] for key in ('origin_identity','file_stat') if key in proof})
                bound = freeze_bound(bound, source=source)
                identity=(originals.identity_key(source.origin_identity),proof['path'],proof['snapshot'],proof['size_bytes'])
                label=evidence_labels.setdefault(identity,f'evidence/{len(evidence_labels)+1:06d}.jsonl')
                evidence[label] = bound
                evidence_origins[label] = original_reference(source,proof)
                proofs.append(dict(label=label))
            candidates.append(dict(candidate_id=candidate['id'], status=candidate['status'], evidence=proofs))
    def execution_proof(source, proof):
        value = dict(name=Path(proof['path']).name,
                     size_bytes=proof['size_bytes'], retention=proof.get('retention'), input_label=None)
        if proof.get('retention') == 'archived':
            bound = dict(path=proof['snapshot'], size_bytes=proof['size_bytes'],
                **{key:proof[key] for key in ('origin_identity','file_stat') if key in proof})
            bound = freeze_bound(bound, source=source)
            identity=(originals.identity_key(source.origin_identity),proof['path'],proof['snapshot'],proof['size_bytes'])
            label=execution_labels.setdefault(identity,f'execution/{len(execution_labels)+1:06d}')
            if label in evidence and evidence[label] != bound:
                raise ValueError('Execution archive alias changed')
            evidence[label] = bound
            evidence_origins[label] = original_reference(source,proof)
            value['input_label'] = label
        return value
    for candidate in candidates:
        candidate['execution_contexts'] = None
        if managed:
            candidate['execution_contexts'] = [dict(call_id=context['call_id'], status=context['status'],
                candidate_id=context['candidate_id'],
                **{key:value for key,value in context.items() if key not in {'call_id','status','candidate_id','scope','parent','change_intent','proofs'}
                    and (value is None or isinstance(value,(str,int,float,bool))
                        or key=='source_code_version' and isinstance(value,dict))},
                proofs=[execution_proof(source, proof) for proof in context['proofs']],
                **{key:execution_proof(source,value) for key,value in context.items()
                    if isinstance(value,dict) and {'path','size_bytes','retention'} <= set(value)})
                for source, context in contexts.get(candidate['candidate_id'], [])]
    if not candidates:
        return dict(items=direct,omitted_count=0,source_nodes=list(source_nodes),capabilities=spec.get('capabilities',[]))
    with _stage(root, 'scid-resolve-') as directory:
        stage = Path(directory)
        (stage / 'inputs').mkdir();(stage / 'scratch').mkdir()
        resolve_objects={}
        for label, proof in evidence.items():
            io_context.remaining_seconds()
            resolve_objects[label]=dict(path=str(_proof(root,proof,context=io_context)),size_bytes=proof['size_bytes'])
        for proof in spec['inputs']:
            if proof['label']=='runtime-python':continue
            frozen = freeze(proof['path'], proof=proof)
            resolve_objects[proof['label']]=frozen
        for label in resolve_objects:
            destination=stage/'inputs'/label
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.touch(exist_ok=False)
        groups={}
        for candidate in candidates:
            owner=candidate_owners[candidate['candidate_id']]
            groups.setdefault(originals.identity_key(owner.origin_identity),[]).append(candidate)
        value=dict(items=[],capabilities=[])
        from . import candidate_refs
        for origin,group in groups.items():
            owner=candidate_owners[group[0]['candidate_id']]
            mapping={candidate_refs.original_id(owner.origin_identity,row['candidate_id']):row['candidate_id'] for row in group}
            if len(mapping)!=len(group):raise ValueError('Adapter candidate identity is ambiguous within its exact origin')
            projected=copy.deepcopy(group)
            for row in projected:
                row['candidate_id']=candidate_refs.original_id(owner.origin_identity,row['candidate_id'])
                for context in row.get('execution_contexts') or []:
                    context['candidate_id']=candidate_refs.original_id(owner.origin_identity,context['candidate_id'])
            request = dict(scope=dict(handoff_protocol=_settings(root, job)['handoff_protocol']),phase='resolve',
                operation_id=security.request_digest(dict(source_nodes=source_nodes,origin=origin)),
                candidate_id=None, artifact_ref=None, root=str(root), source_roots=spec['source_roots'],
                candidates=projected,inputs=spec['inputs'],object_refs=resolve_objects,
                config=spec['config'],target=spec['target'],stage=str(stage),deadline_boottime=deadline)
            resolved = _call(spec, request, deadline - time.clock_gettime(time.CLOCK_BOOTTIME), evidence=leaf_output(root))
            if (not isinstance(resolved,dict) or set(resolved)-{'capabilities'}!={'items'}
                    or not isinstance(resolved['items'],list) or not isinstance(resolved.get('capabilities',[]),list)):
                raise DeliveryUnresolved('delivery_resolver_unavailable')
            for item in resolved['items']:
                if not isinstance(item,dict) or item.get('candidate_id') not in mapping:
                    raise ValueError('Resolver candidate escaped its exact original instance')
                value['items'].append(dict(item,candidate_id=mapping[item['candidate_id']]))
            value['capabilities'].extend(name for name in resolved.get('capabilities',[]) if name not in value['capabilities'])
    declared = value.get('capabilities', []) if isinstance(value, dict) else None
    if (not isinstance(value, dict) or set(value) - {'capabilities'} != {'items'} or not isinstance(value['items'], list)
            or not isinstance(declared, list) or not all(isinstance(name, str) for name in declared) or not set(declared) <= CAPABILITIES):
        raise DeliveryUnresolved('delivery_resolver_unavailable')
    candidate_originals = {row['candidate_id']: row for row in candidates}
    items, seen = list(direct), set()
    static = {}
    for proof in spec['inputs']:
        if proof['label']=='runtime-python':continue
        frozen = freeze(proof['path'], proof=proof)
        static[proof['label']] = frozen
    for item in value['items']:
        if (not isinstance(item, dict) or item.get('candidate_id') not in candidate_originals or item['candidate_id'] in seen
                or item.get('status') not in {'audit_pending', 'unavailable'}):
            raise ValueError('Resolver candidate identity or availability changed')
        seen.add(item['candidate_id'])
        if item['status'] == 'unavailable':
            items.append(dict(candidate_id=item['candidate_id'], status='unavailable', reason=(item.get('reason') if item.get('reason') in {'delivery_code_source_unavailable','delivery_managed_original_missing_or_ambiguous','delivery_model_unavailable','delivery_original_missing'} else 'delivery_source_unavailable')));continue
        evidence_label = item.get('evidence_label')
        if evidence_label not in {proof['label'] for proof in candidate_originals[item['candidate_id']]['evidence']}:
            raise ValueError('Resolver used another candidate original')
        objects = dict(static)
        artifact_origin=None
        try:
            if not isinstance(item.get('objects'),list) or not isinstance(item.get('context'),dict):
                raise ValueError('Resolver objects require bounded references and context')
            for obj in item['objects']:
                if not isinstance(obj, dict):raise ValueError('Resolver object fields changed')
                from_execution = set(obj) == {'label', 'proof_label'}
                if from_execution:
                    authorized = {proof['input_label'] for context in candidate_originals[item['candidate_id']].get('execution_contexts') or []
                                  for proof in context['proofs'] if proof.get('retention') == 'archived'}
                    label = obj['proof_label']
                    if label not in authorized or label not in evidence:
                        raise ValueError('Resolver selected an unbound execution original')
                    bound = evidence[label]
                    name=obj['label']
                    _label(name)
                    if name in objects:raise ValueError('Resolver object label collides with provider input')
                    objects[name]=bound
                    if name=='artifact':artifact_origin=evidence_origins[label]
                    continue
                elif set(obj) != {'label', 'path', 'size_bytes'}:
                    raise ValueError('Resolver object fields changed')
                _label(obj['label'])
                path = Path(obj['path'])
                if obj['label'] in objects or not path.is_absolute() or not any(path.is_relative_to(Path(scope)) for scope in spec['source_roots']):
                    raise ValueError('Resolver object is outside its frozen read scope')
                proof = freeze(path, source_kind='mutable', proof=obj)
                if proof['size_bytes'] != obj['size_bytes']:
                    raise ValueError('Resolver object changed before freezing')
                objects[obj['label']] = proof
                if obj['label']=='artifact':artifact_origin=dict(path=str(path),size_bytes=obj['size_bytes'])
            if 'artifact' not in objects or artifact_origin is None:raise DeliveryUnresolved('delivery_artifact_unavailable')
            owner=candidate_owners[item['candidate_id']]
            raw_id=candidate_refs.original_id(owner.origin_identity,item['candidate_id'])
            selected_context=dict(item['context'])
            code_contexts=[(owner,context) for owner,context in contexts.get(item['candidate_id'],[])
                if context.get('source_code_version') is not None]
            if code_contexts:
                if len(code_contexts)!=1:raise ValueError('Selected code source has ambiguous original calls')
                code_owner,code_context=code_contexts[0]
                accepted=[proof for proof in code_context['proofs'] if Path(proof['path']).name=='accepted.json']
                if len(accepted)!=1:raise ValueError('Selected code source has no exact accepted control')
                original=freeze_bound(dict(path=accepted[0]['snapshot'],size_bytes=accepted[0]['size_bytes'],
                    **{key:accepted[0][key] for key in ('file_stat',) if key in accepted[0]}),source=code_owner)
                selected_context.update(source_code_version=dict(code_context['source_code_version']),accepted=original)
                _selected_code(root,selected_context,context=io_context)
            items.append(dict(candidate_id=item['candidate_id'], status='audit_pending',
                              context=selected_context, ledger=evidence[evidence_label], objects=objects,artifact_origin=artifact_origin,
                              **(dict(origin_identity=owner.origin_identity,original_candidate_id=raw_id) if raw_id!=item['candidate_id'] else {}),
                              **(dict(audit_required='audit' in spec['capabilities']) if 'capabilities' in spec else {})))
        except DeliveryUnresolved as error:
            items.append(dict(candidate_id=item['candidate_id'], status='unavailable', reason=error.code))
    for candidate in candidates:
        if candidate['candidate_id'] not in seen:
            items.append(dict(candidate_id=candidate['candidate_id'], status='unavailable', reason='delivery_source_unavailable'))
    return dict(items=items,omitted_count=0,source_nodes=list(source_nodes),
        **(dict(capabilities=sorted(set(declared))) if declared else {}))


def public_sources(refs, audits=(), *, nominated_ids=None):
    facts = {}
    for audit in audits:
        facts[audit['candidate_id']] = {key:audit[key] for key in ('qualification', 'artifact_binding', 'scope')}
    preview=refs['items'] if nominated_ids is None else [row for row in refs['items'] if row['candidate_id'] in nominated_ids]
    rows = [dict(candidate_id=row['candidate_id'],
                         availability='available' if row['status'] in {'audit_pending', 'available'} else row['status'],
                         **(dict(reason=row['reason']) if 'reason' in row else {}),
                         audit=facts.get(row['candidate_id'], dict(qualification='not_observed')))
                         for row in preview[:16]]
    result = dict(items=[], omitted_count=refs['omitted_count'] + len(refs['items']),
                  qualification='Availability is not qualification. Read bound candidate audit receipts for exact artifact scope; public audit does not establish target performance.')
    if refs.get('catalogue'):
        result['catalogue'] = 'inputs/delivery-candidates.json'
    for row in rows:
        proposed = dict(result, items=[*result['items'], row], omitted_count=result['omitted_count'] - 1)
        if len(_bytes(proposed)) <= 4096:
            result = proposed
    return result


def _proof_path(root, proof, *, source=None, context=None):
    if (not isinstance(proof,dict) or set(proof)-{'origin_identity','file_stat'}!={'path','size_bytes'}
            or not isinstance(proof['path'],str) or type(proof['size_bytes']) is not int or proof['size_bytes']<0):
        raise ValueError('Exact delivery file reference required')
    name=Path(proof['path'])
    if '..' in name.parts or name.as_posix()!=proof['path']:
        raise ValueError('Delivery object is outside its exact original binding')
    if source is not None:
        return _file(originals.resolve_ref(source,proof,context=context))
    if name.is_absolute():
        return originals._check_identity(_file(name),proof,context=context)
    return _file(originals.resolve_root_ref(root,proof,context=context))


def _proof(root, proof, *, source=None, context=None):
    return _proof_path(root,proof,source=source,context=context)


def _selected_code(root, context_value, *, context=None, materialize=False):
    """Resolve the existing accepted/compiler pin without making exports permanent."""
    version=context_value.get('source_code_version')
    if version is None:return None
    from . import experiments as e, code_versions, execution_inputs
    accepted_ref=context_value.get('accepted')
    if not isinstance(accepted_ref,dict):raise ValueError('Selected code has no original accepted control')
    accepted_path=_proof_path(root,accepted_ref,context=context)
    accepted=c.read(accepted_path)
    directory=Path(accepted['interface']['config_record']['record_dir'])/'calls'/accepted['call_id']
    if accepted_path!=directory/'accepted.json' or not directory.is_relative_to(Path(root)):
        raise ValueError('Selected code escaped its exact original call')
    observation=c.read(c.regular(directory/'execution-observation.json'))
    config=dict(accepted['interface']['config_record'],execution_interface=accepted['interface'])
    e.verify_observation(observation,lambda proof:Path(proof['path']),interface=accepted['interface'],
        context=context,verify_files=False)
    if observation.get('source_code_version')!=version:
        raise ValueError('Selected code differs from its saved original version')
    if 'code_version' in accepted:
        e._code_version_binding(config,accepted)
        if accepted['code_version']!=version:raise ValueError('Selected code changed its accepted call')
        step=accepted['scope']['step'];call=accepted['call_id']
    else:
        metadata=e._source_control_metadata(config,accepted)
        if metadata is None or metadata['code_version']!=version:
            raise ValueError('Selected code lost its exact compiler binding')
        step=metadata['subject']['producer_step']
        call=execution_inputs._compiler_call_id(root,step,metadata['compiled']['compiler'])
    code_versions.verify(root,step=step,call_id=call,code_version=version,context=context)
    result=dict(root=Path(root),step=step,call_id=call,code_version=version)
    if materialize:
        tree=code_versions.materialize(root,step=step,call_id=call,code_version=version,context=context)
        result['source']=e.proof(tree/version['entry'],context=context)
    return result


def _verify_selected_originals(root, item, source_nodes, *, context=None):
    """Compare selected wire2 paths with the saved producer descriptors only."""
    wanted={proof['path']:proof for proof in item['objects'].values()}
    for receipt in source_nodes:
        job,_,seal,seal_ref=history._source(root,receipt['step'])
        if seal_ref!=receipt['seal_ref']:raise ValueError('Selected delivery producer control changed')
        for material in seal.get('materials',[]):
            if material['source'] in wanted:originals.material_path(root,material)
        fact=history.read_observation(root,job,seal)
        if fact is None:continue
        candidate=next((row for row in fact['candidates'] if row['id']==item['candidate_id']),None)
        if candidate is None:continue
        proofs=list(candidate.get('evidence',[]))
        for execution_context in fact.get('contexts',[]):
            if execution_context.get('candidate_id')==item['candidate_id']:
                from . import lineage
                proofs.extend(execution_context['proofs']);proofs.extend(lineage.context_artifacts(execution_context))
        for call in fact.get('managed_calls',[]):
            proofs.extend(call.get('outputs',[]));proofs.extend(call.get('originals',[]))
        owner=originals.node(root,receipt['step'])
        for proof in proofs:
            if proof.get('retention')!='archived':continue
            path=Path(proof['snapshot'])
            actual=str(path if path.is_absolute() else Path(owner.original_root)/path)
            if actual not in wanted:continue
            _proof_path(root,dict(path=proof['snapshot'],size_bytes=proof['size_bytes'],
                **{key:proof[key] for key in ('file_stat',) if key in proof}),source=owner,context=context)
    _selected_code(root,item.get('context',{}),context=context)


def validate_sources(root, refs, *, job=None):
    _adapter(root, job)
    for receipt in refs['source_nodes']:
        if history._source(root, receipt['step'])[3] != receipt['seal_ref']:
            raise ValueError('Delivery source scientific identity changed')
    seen = {}
    for item in refs['items']:
        if 'original_candidate_id' in item or 'origin_identity' in item:
            from . import candidate_refs
            if (not {'original_candidate_id','origin_identity'} <= set(item)
                    or candidate_refs.origin_candidate(item['origin_identity'],item['original_candidate_id'])!=item['candidate_id']):
                raise ValueError('Adapter candidate differs from its exact original instance')
        if item['status'] == 'audit_pending':
            _verify_selected_originals(root,item,refs['source_nodes'])
            for proof in [item['ledger'], *item['objects'].values()]:
                identity = (proof['path'], proof['size_bytes'])
                if proof['path'] in seen:
                    if seen[proof['path']] != identity:raise ValueError('Conflicting delivery aliases for one original')
                else:
                    _proof(root, proof)
                    seen[proof['path']] = identity
            origin=item['artifact_origin']
            if (not isinstance(origin,dict) or set(origin)!={'path','size_bytes'}
                    or not isinstance(origin['path'],str) or not Path(origin['path']).is_absolute()
                    or '..' in Path(origin['path']).parts or Path(origin['path']).as_posix()!=origin['path']
                    or origin['size_bytes']!=item['objects']['artifact']['size_bytes']):
                raise ValueError('Delivery artifact lost its exact original source')
    return refs


def _choice(root, job, result):
    if result.get('next_action') != 'deliver':
        raise DeliveryUnresolved('delivery_authorization_required')
    from . import candidate_refs
    chosen=result.get('candidate_recommendation')
    if chosen is not None and (not isinstance(chosen,str) or chosen not in candidate_refs.available_ids(root,job)):
        raise DeliveryUnresolved('selection_candidate_unavailable')
    from . import shortlist
    if _settings(root, job).get('selection_policy') is not None:
        if job.get('selection',{}).get('normal_step') is not None:
            if job['selection']['normal_step']!=job['step']:raise DeliveryUnresolved('selection_comparison_required')
            shortlist._screen(root,job['selection'],job=job)
            expected=job['selection']
        elif job.get('fork_source') and job.get('selection') is not None:
            shortlist._screen(root,job['selection'],job=job)
            expected=job['selection']
        else:
            expected = shortlist.marker(root,job['step'])
        if expected is None:
            raise DeliveryUnresolved('selection_screen_unavailable')
        if job.get('selection') != expected:
            raise DeliveryUnresolved('selection_comparison_required')
    return shortlist.choice(root, job, result) if job.get('selection') else result.get('candidate_recommendation')


def prepare_binding(root, job, result):
    if result.get('next_action') == 'deliver':
        chosen = _choice(root, job, result)
        if chosen is not None:
            refs = job.get('delivery_sources') or budget.closeout_state(root)['begin']['delivery_sources']
            try:
                prepared=selected_sources(root, refs, chosen, prepare=True, job=job)
                rows=[row for row in prepared['items'] if row['candidate_id']==chosen]
                if len(rows)==1 and rows[0]['status']=='unavailable' and rows[0].get('reason')=='delivery_code_source_unavailable':
                    preparation_failure(root,'implementation_source_missing',refs['source_nodes'],candidate_id=chosen,decision_step=job['step'])
                    raise PreparationUnavailable('implementation_source_missing')
            except DeliveryUnresolved as error:
                if error.code == 'delivery_source_unavailable':
                    raise
                raise PreparationUnavailable(error.code) from error
    return binding(root, job, result)


def binding(root, job, result):
    from . import candidate_refs
    # Public callers may supply the unmodified scientific return; c.validate
    # supplies the already resolved host envelope. Normalize only public refs.
    if job.get('worker_view_version')==1:
        result=candidate_refs.control_payload(job,result,root=root)
    begin = budget.closeout_state(root)['begin']
    ordinary = begin is not None and begin.get('authorization_step') == job['step']
    if job['role'] != 'A' or not (job.get('purpose') == 'closeout' or ordinary):
        raise ValueError('Delivery requires its exact frozen A authorization')
    path = root / 'jobs' / f'{job["step"]:03d}.json'
    if c.read(path) != job:
        raise ValueError('Delivery decision job changed')
    raw = c.validate(c.workspace(root, job['step']))
    chosen = result.get('candidate_recommendation')
    if chosen != raw.get('candidate_recommendation'):
        raise ValueError('Delivery choice differs from sealed original')
    chosen = _choice(root, job, raw)
    if chosen is None:
        raise DeliveryUnresolved('delivery_no_decision')
    refs = selected_sources(root, job.get('delivery_sources') or begin['delivery_sources'], chosen, job=job)
    rows = [row for row in refs['items'] if row['candidate_id'] == chosen]
    if len(rows) != 1 or rows[0]['status'] != 'audit_pending':
        raise DeliveryUnresolved('delivery_source_unavailable')
    item = rows[0]
    from . import engineering
    for repair_step in engineering.steps(root,job['step']):
        owner=originals.node(root,repair_step)
        repair=originals.source_job(owner)['engineering_repair']
        producer=originals.dependency(owner,repair['source']['step'],seal_ref=repair['source']['seal_ref'])
        if history.source_seal_ref(producer)!=repair['source']['seal_ref']:
            raise ValueError('Engineering repair candidate source seal changed')
        original=(candidate_refs.origin_candidate(producer.origin_identity,repair['candidate_id'])
            if producer.inherited else repair['candidate_id'])
        if original==chosen:
            raise DeliveryUnresolved('delivery_engineering_original_unrepaired')
    for row in budget.closeout_state(root)['actions']:
        outcome = row['outcome']
        if (outcome is not None and outcome['result']['status'] == 'rejected'
                and outcome['result'].get('failure_phase') == 'audit'
                and row['intent']['binding']['candidate_id'] == chosen
                and row['intent']['binding']['source']['artifact_origin'] == item['artifact_origin']):
            # The original author remains a valid historical binding. A new
            # decision cannot replay the same failed candidate bytes.
            if row['intent']['decision']['step'] < job['step']:
                raise DeliveryUnresolved('delivery_candidate_failed')
    # c.validate already checks this entire frozen source pack once. Re-reading
    # every candidate/static object here would multiply mechanical I/O by count.
    record = c.host_record(c.workspace(root, job['step']))
    decision = dict(step=job['step'], job_ref=c.record_ref(path),
                    raw_ref=c.file_ref(execution.raw(root, job['step'])), seal_ref=c.record_ref(record / 'result-identity.json'))
    result = dict(candidate_id=chosen, decision=decision, source=item,
                  target=_settings(root, job)['delivery_policy']['adapter']['target'])
    result['operation_id'] = security.request_digest(dict(settings_ref=c.record_ref(root / 'settings.json'), **result))
    return result


def _result(binding, status, note, *, cleanup_confirmed=True):
    return dict(status=status, operation_id=binding['operation_id'], candidate_id=binding['candidate_id'],
                artifact_ref=binding['source']['objects']['artifact'], note=note,
                cleanup_confirmed=cleanup_confirmed)


def leaf_output(root):
    return Path(root) / 'host-delivery/leaf-output'


def leaf_refs(root, operation_id):
    """Exact host diagnostics for one operation; outcome receipts reference these."""
    directory = leaf_output(root)
    return [dict(path=path.relative_to(root).as_posix())
            for path in sorted(directory.glob(operation_id + '-*.json'))] if directory.is_dir() else []


def _tail(path):
    """Bounded tail of a leaf-written scratch log; never follows a link."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    with os.fdopen(descriptor, 'rb') as stream:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):return None
        size = status.st_size
        stream.seek(max(0, size - MAX_LEAF_TAIL))
        return size, stream.read(MAX_LEAF_TAIL)


def _keep(evidence, request, outcome, returncode, streams):
    """Host-only diagnostics beside the action outcome, keyed by operation/phase.

    Never a scientific original: no workspace, history or model brief reads it.
    """
    if evidence is None:return
    try:
        directory = Path(evidence);directory.mkdir(parents=True, exist_ok=True)
        if directory.resolve() != directory:return
        stem = next(f"{request['operation_id']}-{request['phase']}-{n}" for n in range(1, 1000)
                    if not (directory / f"{request['operation_id']}-{request['phase']}-{n}.json").exists())
        index = dict(operation_id=request['operation_id'], phase=request['phase'], outcome=outcome, returncode=returncode, streams={})
        for label, (size, data) in streams.items():
            entry = dict(total_bytes=size, kept_bytes=len(data), truncated=size > len(data), path=None)
            if data:
                path = directory / f'{stem}.{label}'
                with path.open('xb') as stream:stream.write(data)
                path.chmod(0o400);entry.update(path=path.name)
            index['streams'][label] = entry
        with (directory / (stem + '.json')).open('x') as stream:stream.write(json.dumps(index, sort_keys=True) + '\n')
        (directory / (stem + '.json')).chmod(0o400)
    except (OSError, StopIteration):
        pass  # Diagnostics never change the bounded delivery outcome.


def _call(spec, request, seconds, *, on_process=None, evidence=None):
    if seconds <= 0:return None
    replay_deadline = None
    if request.get('phase') == 'replay' or 'deadline_boottime' in request:
        bound = request.get('deadline_boottime')
        if type(bound) not in (int, float) or not 0 < bound < float('inf'):
            return None
        now = time.clock_gettime(time.CLOCK_BOOTTIME)
        replay_deadline = min(bound, now + seconds)
        if replay_deadline <= now:return None
    code=None
    if request.get('context',{}).get('source_code_version') is not None:
        accepted=Path(request['context']['accepted']['path'])
        if not accepted.is_absolute() or len(accepted.parents)<7:
            raise ValueError('Selected code has no exact instance control path')
        code=_selected_code(accepted.parents[6],request['context'],materialize=True)
        label=('inputs/'+code['code_version']['entry'] if request['context'].get('kind')=='managed_local_result' else 'source.py')
        expected={key:code['source'][key] for key in ('path','size_bytes')}
        if request.get('object_refs',{}).get(label)!=expected:
            raise ValueError('Selected code lost its existing readonly input label')
    def cleanup_code():
        if code is None:return
        from . import code_versions
        try:code_versions.cleanup_export(code['root'],step=code['step'],call_id=code['call_id'])
        except (OSError,ValueError):keep('code_export_cleanup_failed')
    runtime._subreaper()  # Same actual ownership cleanup used by native runners.
    output, errors, totals = bytearray(), bytearray(), dict(stdout=0, stderr=0)
    def keep(outcome, returncode=None):
        streams = dict(stdout=(totals['stdout'], bytes(output[-MAX_LEAF_TAIL:])), stderr=(totals['stderr'], bytes(errors)))
        if request.get('phase') in ('audit', 'smoke', 'replay') and request.get('stage'):
            for name in ('stdout', 'stderr'):
                found = _tail(Path(request['stage']) / 'scratch' / f"{request['phase']}.{name}")
                if found is not None:streams[request['phase'] + '.' + name] = found
        _keep(evidence, request, outcome, returncode, streams)
    with tempfile.TemporaryFile(mode='w+',encoding='utf-8') as input_file, tempfile.TemporaryFile() as output_file:
        json.dump(request,input_file,ensure_ascii=False,allow_nan=False,sort_keys=True);input_file.flush();input_file.seek(0)
        if replay_deadline is not None and time.clock_gettime(time.CLOCK_BOOTTIME) >= replay_deadline:
            return None
        try:
            process = subprocess.Popen([*spec['launcher_argv'], spec['adapter']], stdin=input_file,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={'PATH': os.defpath}, start_new_session=True)
        except OSError as error:
            cleanup_code()  # Popen established that this leaf never started.
            if request.get('phase')=='audit':raise DeliveryUnresolved('delivery_audit_process_unavailable') from error
            raise
        identity = None
        failed = False
        cleanup = False
        child_completed = False
        try:
            identity = runtime.process_identity(process.pid, request['operation_id'])
            if on_process is not None:
                on_process(request['phase'], identity)
            deadline = replay_deadline if replay_deadline is not None else time.clock_gettime(time.CLOCK_BOOTTIME) + seconds
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                selector.register(process.stderr, selectors.EVENT_READ)
                while selector.get_map() or process.poll() is None:
                    remaining = deadline - time.clock_gettime(time.CLOCK_BOOTTIME)
                    if remaining <= 0:failed = True;break
                    for key, _ in selector.select(min(.1, remaining)):
                        block = os.read(key.fileobj.fileno(), 65536)
                        if not block:selector.unregister(key.fileobj);continue
                        if key.fileobj is process.stdout:
                            output_file.write(block);output.extend(block);del output[:-MAX_LEAF_TAIL];totals['stdout']+=len(block)
                        else:errors.extend(block);del errors[:-MAX_LEAF_TAIL];totals['stderr'] += len(block)
            if not failed:
                process.wait(timeout=5)
                child_completed = True
        finally:
            # Popen transfers child ownership before identity or durable receipt
            # publication. Every later failure must reap that exact child.
            # Without identity we can stop our child, but cannot certify a group.
            try:
                if process.poll() is None:
                    try:
                        if identity is not None and runtime.process_matches(identity, identity['task_ref']):
                            os.killpg(identity['pgid'], signal.SIGKILL)
                    except (OSError, ValueError):
                        pass  # A failed group proof cannot forfeit direct child ownership.
                    if process.poll() is None:process.kill()
                process.wait(timeout=5)
                if identity is not None:
                    cleanup = runtime.cleanup_owned_group(identity) == 'observed_group_cleaned'
            except (OSError, ValueError, subprocess.TimeoutExpired):
                cleanup = False  # Keep the original call error, if one is propagating.
            finally:
                for stream in (process.stdout, process.stderr):
                    try:stream.close()
                    except OSError:pass
                outcome = ('cleanup_unknown' if not cleanup else 'time_or_byte_limit' if failed
                    else 'child_completed' if child_completed and process.returncode == 0
                    else 'child_failed' if child_completed else 'child_stopped')
                keep(outcome, process.returncode)
        if not cleanup:
            raise DeliveryUnresolved('delivery_cleanup_unknown')
        if failed:
            if request.get('phase') == 'audit':
                raise DeliveryUnresolved('delivery_audit_timeout')
            return None
        if process.returncode:
            if request.get('phase')=='audit':raise DeliveryUnresolved('delivery_audit_process_failed')
            return None
        if replay_deadline is not None and time.clock_gettime(time.CLOCK_BOOTTIME) >= replay_deadline:
            return None
        output_file.seek(0)
        try:value=json.load(output_file)
        except (ValueError,UnicodeDecodeError):return None
        if isinstance(value,dict) and value.get('status') not in {None,'unknown'}:cleanup_code()
        return value


def _staged(root, spec, source, operation_id, stage, *, job=None, context=None):
    """Use zero-byte mountpoints for actual selected readonly originals."""
    from . import storage_policy as storage
    inventory=storage.Inventory(_settings(root,job).get('storage_policy'),context=context)
    (stage/'inputs/harness').mkdir(parents=True)
    (stage/'scratch').mkdir()
    nodes=(job or {}).get('delivery_sources',{}).get('source_nodes',(job or {}).get('history',{}).get('nodes',[]))
    _verify_selected_originals(root,source,nodes,context=context)
    objects={}
    for name,proof in source['objects'].items():
        _label(name);inventory.entry();inventory.file(proof['size_bytes'])
        objects[name]=dict(path=str(_proof(root,proof,context=context)),size_bytes=proof['size_bytes'])
    code=_selected_code(root,source.get('context',{}),context=context,materialize=True)
    if code is not None:
        name=('inputs/'+code['code_version']['entry'] if source['context'].get('kind')=='managed_local_result' else 'source.py')
        if name in objects:raise ValueError('Selected code collides with another original')
        objects[name]={key:code['source'][key] for key in ('path','size_bytes')}
    for name in objects:
        destination=stage/'inputs'/name
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.touch(exist_ok=False)
    candidate=source['candidate_id']
    if 'original_candidate_id' in source:
        from . import candidate_refs
        if candidate_refs.origin_candidate(source['origin_identity'],source['original_candidate_id'])!=candidate:
            raise ValueError('Adapter candidate differs from its exact original instance')
        candidate=source['original_candidate_id']
    return dict(scope=dict(handoff_protocol=_settings(root,job)['handoff_protocol']),operation_id=operation_id,
        candidate_id=candidate,artifact_ref=source['objects']['artifact'],artifact_origin=source['artifact_origin'],
        context=source['context'],object_refs=objects,inputs=spec['inputs'],config=spec['config'],stage=str(stage))


@owner_effect
def leaf_phase(root, source, operation_id, phase, seconds, *, on_process=None, job=None):
    """One control screening phase on exact frozen originals; never promotes or confirms."""
    from .protocol import require_current
    require_current(root)
    if (phase not in {'smoke', 'audit'} or source.get('status') != 'audit_pending'
            or type(seconds) not in (int, float) or not 0 < seconds < float('inf')):
        raise ValueError('Control screening requires an exact pending original and bounded phase')
    deadline = time.clock_gettime(time.CLOCK_BOOTTIME) + seconds
    spec = _adapter(Path(root).resolve(), job, context=_Deadline(deadline))
    with _stage(root, 'scid-selection-') as directory:
        request = dict(_staged(root, spec, source, operation_id, Path(directory), job=job, context=_Deadline(deadline)),
            phase=phase, deadline_boottime=deadline)
        if phase == 'audit':
            from . import audit_recovery
            value, _, _ = audit_recovery.run(root, spec, source, request, seconds, on_process=on_process, job=job, deadline=deadline)
            return value
        value = _call(spec, request, max(0, deadline - time.clock_gettime(time.CLOCK_BOOTTIME)), on_process=on_process, evidence=leaf_output(root))
    if value is None:
        return dict(status='failed' if phase == 'audit' else 'unknown', note='bounded_' + phase + '_unverified', cleanup_confirmed=True)
    allowed = {'ok', 'failed', 'unknown'} if phase == 'smoke' else {'audited', 'failed', 'unknown'}
    if (not isinstance(value, dict) or set(value) != {'status', 'operation_id', 'candidate_id', 'artifact_ref', 'note'}
            or any(value.get(key) != request[key] for key in ('operation_id', 'candidate_id', 'artifact_ref'))
            or value['status'] not in allowed or not isinstance(value['note'], str) or len(value['note'].encode()) > 1024):
        return dict(status='failed' if phase == 'audit' else 'unknown', note='invalid_' + phase + '_result', cleanup_confirmed=True)
    return dict(status=value['status'], note=value['note'], cleanup_confirmed=True)


@owner_effect
def invoke(root, intent, max_seconds, *, on_process=None, audit_only=False):
    """Caller already durably accepted this action; recovered submits never call here."""
    from .protocol import require_current
    require_current(root)
    root = Path(root).resolve()
    if type(max_seconds) not in (int, float) or not 0 < max_seconds < float('inf'):
        raise ValueError('Bounded action seconds required')
    if audit_only:
        from . import candidate_audit
        candidate_audit.validate_intent(root, intent)
        actions = [dict(intent=intent, outcome=None)]
    else:
        actions = budget.closeout_state(root)['actions']
    stored = next((action for action in actions if action['intent']['action_id'] == intent.get('action_id')), None)
    if stored is None or stored['intent'] != intent:
        raise ValueError('Delivery action was not durably accepted in the control ledger')
    if stored['outcome'] is not None:
        return stored['outcome']['result']
    anchor = intent['advance_seconds']
    used = budget.snapshot(root)['advance_seconds'] - anchor
    if used < 0:raise ValueError('Delivery action time moved backwards')
    max_seconds = min(max_seconds, intent['max_seconds'] - used)
    if intent.get('final_return') is None:
        max_seconds = min(max_seconds, budget.snapshot(root)['seconds_remaining'])
    if max_seconds <= 0:return _result(intent['binding'], 'unknown', 'action_time_limit')
    deadline = time.clock_gettime(time.CLOCK_BOOTTIME) + max_seconds
    bound = intent['binding']
    if intent['operation_id'] != bound['operation_id'] or intent['decision'] != bound['decision']:
        raise ValueError('Delivery action identity differs from frozen decision')
    decision = bound['decision'];step = decision['step']
    record = c.host_record(c.workspace(root, step))
    for path, key in [(root / 'jobs' / f'{step:03d}.json', 'job_ref'),
                      (execution.raw(root, step), 'raw_ref'), (record / 'result-identity.json', 'seal_ref')]:
        if (c.file_ref(_file(path)) if key=='raw_ref' else c.record_ref(_file(path))) != decision[key]:
            raise ValueError('Delivery scientific decision changed')
    job = c.read(root / 'jobs' / f'{step:03d}.json')
    spec = _adapter(root, job, context=_Deadline(deadline))
    if audit_only:
        candidate_audit.validate_binding(root, intent)
    elif binding(root, job, c.validate(c.workspace(root, step))) != bound:
        raise ValueError('Delivery input binding changed')
    if intent['action_kind'] not in (('audit',) if audit_only else ('submit', 'confirm')):
        raise ValueError('Unknown delivery action')
    with _stage(root, 'scid-delivery-') as directory:
        request = dict(_staged(root, spec, bound['source'], bound['operation_id'], Path(directory), job=job,
            context=_Deadline(deadline)), deadline_boottime=deadline)
        phases = ('audit',) if audit_only else (('audit', 'promote') if bound['source'].get('audit_required', True)
            else ('promote',)) if intent['action_kind'] == 'submit' else ('confirm',)
        for phase in phases:
            remaining = deadline - time.clock_gettime(time.CLOCK_BOOTTIME)
            if phase == 'audit':
                from . import audit_recovery
                value, request, deadline = audit_recovery.run(root, spec, bound['source'], request, remaining, on_process=on_process, author_required=audit_only, job=job, deadline=deadline)
                if value['status'] == 'audited':
                    if audit_only:
                        return dict(_result(bound, 'audited', value['note']), cleanup_confirmed=value['cleanup_confirmed'])
                    continue
                return dict(_result(bound, value['status'], value['note'], cleanup_confirmed=value['cleanup_confirmed']), failure_phase='audit',recovery=value.get('recovery','none'),**(dict(diagnostic=value['diagnostic']) if 'diagnostic' in value else {}))
            try:
                value = _call(spec, dict(request, phase=phase, target=spec['target']), remaining, on_process=on_process,
                              evidence=leaf_output(root)) if remaining > 0 else None
            except DeliveryUnresolved as error:
                if error.code != 'delivery_cleanup_unknown':raise
                return _result(bound, 'unknown', 'cleanup_unknown', cleanup_confirmed=False)
            if value is None:
                result = _result(bound, 'failed' if phase == 'audit' else 'unknown', 'bounded_' + phase + '_unverified')
                return dict(result, failure_phase='audit') if phase == 'audit' else result
            if (not isinstance(value, dict) or set(value) != {'status', 'operation_id', 'candidate_id', 'artifact_ref', 'note'}
                    or any(value.get(key) != request[key] for key in ('operation_id', 'candidate_id', 'artifact_ref'))
                    or not isinstance(value['note'], str) or len(value['note'].encode()) > 1024):
                result = _result(bound, 'failed' if phase == 'audit' else 'unknown', 'invalid_' + phase + '_result')
                return dict(result, failure_phase='audit') if phase == 'audit' else result
            value=dict(value,candidate_id=bound['candidate_id'])
            if phase == 'audit' and value['status'] == 'audited':
                if audit_only:
                    return dict(value, cleanup_confirmed=True)
                continue
            if value['status'] not in ('confirmed', 'failed', 'unknown') or phase == 'audit' and value['status'] == 'confirmed':
                result = _result(bound, 'failed' if phase == 'audit' else 'unknown', 'invalid_phase_result')
                return dict(result, failure_phase='audit') if phase == 'audit' else result
            return dict(value, cleanup_confirmed=True, **(dict(failure_phase=phase) if value['status'] == 'failed' else {}))
    raise AssertionError('Delivery phase produced no bounded outcome')
