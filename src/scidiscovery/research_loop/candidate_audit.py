"""One explicit A-selected, scratch-only adapter audit; immutable node facts."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

from . import configuration_updates
from . import budget, contracts as c, delivery, execution, native

SCOPE = 'Adapter-scoped qualification, not target evidence or submission.'


def enabled(settings):
    return settings.get('candidate_audit_policy') == {'version': 1}


def _directory(root, step):
    path = c.host_record(c.workspace(root, step)) / 'candidate-audit'
    if path.resolve() != path:
        raise ValueError('Candidate audit original location changed')
    return path


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,allow_nan=False,sort_keys=True)
        stream.write('\n');stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o400)


def _decision(root, step):
    record = c.host_record(c.workspace(root, step))
    return dict(step=step, job_sha256=c.digest(root / 'jobs' / f'{step:03d}.json'),
                raw_sha256=c.digest(execution.raw(root, step)), seal_sha256=c.digest(record / 'result-identity.json'))


def _operation(root, decision, candidate):
    return hashlib.sha256(delivery._bytes(dict(decision=decision, candidate_id=candidate,
        settings_sha256=c.digest(root / 'settings.json')))).hexdigest()


def _validate_request(root, intent):
    decision = intent['decision']
    stored_fields = {'action_id', 'action_kind', 'operation_id', 'decision', 'candidate_id', 'max_seconds', 'advance_seconds'}
    if (set(intent) - {'binding'} != stored_fields or intent['action_kind'] != 'audit'
            or intent['operation_id'] != _operation(root, decision, intent['candidate_id'])
            or intent['action_id'] != intent['operation_id']
            or intent['max_seconds'] != configuration_updates.for_step(root, decision['step'])['delivery_policy']['submit_seconds']
            or type(intent['advance_seconds']) not in (int, float)
            or not math.isfinite(intent['advance_seconds']) or intent['advance_seconds'] < 0):
        raise ValueError('Candidate audit accepted budget or identity changed')
    if (set(decision) != {'step','job_sha256','raw_sha256','seal_sha256'}
            or type(decision['step']) is not int or decision['step'] <= 0
            or any(not isinstance(decision[key],str) or len(decision[key]) != 64
                or any(char not in '0123456789abcdef' for char in decision[key])
                for key in ('job_sha256','raw_sha256','seal_sha256'))):
        raise ValueError('Candidate audit declared author identity changed')
    job = c.read(c.regular(root/'jobs'/f'{decision["step"]:03d}.json'))
    if (job['role'] != 'A' or job['handoff_protocol'] < 9
            or c.digest(root/'jobs'/f'{decision["step"]:03d}.json') != decision['job_sha256']):
        raise ValueError('Candidate audit declared requester changed')


def require_eligibility(root, intent):
    """Validate the scientific decision separately from its durable permission."""
    decision = intent['decision']
    if decision != _decision(root,decision['step']):
        raise ValueError('Candidate audit scientific author changed')
    job = c.read(c.regular(root/'jobs'/f'{decision["step"]:03d}.json'))
    raw = c.validate(c.workspace(root,decision['step']))
    if job.get('purpose') is not None:
        from . import engineering
        marker = job['closeout_begin']
        file = root/'control-ledger'/f'{marker["seq"]:06d}.json'
        begin = c.read(c.regular(file))
        if (job['purpose'] != 'closeout' or job['source_cutoff'] != begin['source_cutoff']
                or job['step'] < begin['first_step'] or job['delivery_sources'] != engineering.sources(root,job['step'])
                or marker['sha256'] != c.digest(file)):
            raise ValueError('Candidate audit closeout boundary changed')
    if raw['next_action'] != 'audit' or raw['candidate_recommendation'] != intent['candidate_id']:
        raise ValueError('Candidate audit differs from the original scientific request')


def validate_intent(root, intent):
    _validate_request(root, intent)
    path = _directory(root, intent['decision']['step']) / 'intent.json'
    if c.read(c.regular(path)) != {key: value for key, value in intent.items() if key != 'binding'}:
        raise ValueError('Candidate audit accepted identity changed')


def _dispatch_binding(root,intent):
    directory = _directory(root,intent['decision']['step'])
    return dict(operation_id=intent['operation_id'],intent_sha256=c.digest(c.regular(directory/'intent.json')),
        sources_sha256=c.digest(c.regular(directory/'sources.json')),binding_sha256=c.digest(c.regular(directory/'binding.json')))


@c.verified_digests()
def _validate_objects(root, intent, binding, refs):
    _validate_request(root, intent)
    require_eligibility(root,intent)
    job = c.read(c.regular(root/'jobs'/f'{intent["decision"]["step"]:03d}.json'))
    delivery.validate_sources(root, refs, job=job)
    rows = [row for row in refs['items'] if row['candidate_id'] == intent['candidate_id']]
    if (len(rows) != 1 or rows[0] != binding['source'] or binding['decision'] != intent['decision']
            or binding['operation_id'] != intent['operation_id'] or binding['candidate_id'] != intent['candidate_id']
            or binding['target'] != delivery._adapter(root, job)['target']):
        raise ValueError('Candidate audit no longer binds its selected original')


@c.verified_digests()
def validate_binding(root, intent):
    validate_intent(root, intent)
    directory = _directory(root, intent['decision']['step'])
    binding = c.read(c.regular(directory / 'binding.json'))
    if binding != intent['binding']:
        raise ValueError('Candidate audit object binding changed')
    refs = c.read(c.regular(directory / 'sources.json'))
    _validate_objects(root,intent,binding,refs)
    dispatch = directory/'dispatch.json'
    if dispatch.exists() and c.read(c.regular(dispatch)) != _dispatch_binding(root,intent):
        raise ValueError('Candidate audit dispatch identity changed')


@c.verified_digests()
def _outcome(root, step):
    directory = _directory(root, step)
    path = directory / 'outcome.json'
    if not path.exists():
        return None
    value = c.read(c.regular(path))
    if value['decision'] != _decision(root, step):
        raise ValueError('Candidate audit receipt scientific source changed')
    raw = c.read(execution.raw(root, step))
    from . import candidate_refs
    raw=candidate_refs.control_payload(c.read(root/'jobs'/f'{step:03d}.json'),raw,root=root)
    if (type(value['accepted']) is not bool or value['accepted'] != (directory / 'intent.json').exists()
            or value['candidate_id'] != raw.get('candidate_recommendation')
            or raw.get('next_action') != 'audit'
            or type(value['result'].get('note')) is not str or len(value['result']['note'].encode()) > 1024):
        raise ValueError('Candidate audit outcome differs from its original request')
    if value['accepted']:
        intent = c.read(c.regular(directory / 'intent.json'))
        validate_intent(root, intent)
        if value['intent_sha256'] != c.digest(directory / 'intent.json'):
            raise ValueError('Candidate audit intent receipt changed')
        if value.get('binding_sha256') is not None:
            binding = c.read(c.regular(directory / 'binding.json'))
            validate_binding(root, dict(intent, binding=binding))
            if value['binding_sha256'] != c.digest(directory / 'binding.json'):
                raise ValueError('Candidate audit binding receipt changed')
        result = value['result']
        expected_artifact = binding['source']['objects']['artifact']['sha256'] if value.get('binding_sha256') is not None else None
        if (result.get('operation_id') != intent['operation_id'] or result.get('candidate_id') != intent['candidate_id']
                or result.get('artifact_sha256') != expected_artifact):
            raise ValueError('Candidate audit result object identity changed')
        process_path = directory / 'process.json'
        if value.get('process_sha256') is not None:
            if c.digest(c.regular(process_path)) != value['process_sha256']:
                raise ValueError('Candidate audit process original changed')
            process = c.read(process_path)
            identity = process.get('identity', {})
            if (process.get('operation_id') != intent['operation_id'] or process.get('phase') != 'audit'
                    or identity.get('task_sha256') != intent['operation_id']
                    or any(type(identity.get(key)) is not int or identity[key] <= 0 for key in ('pid', 'pgid', 'sid', 'start_ticks'))):
                raise ValueError('Candidate audit process identity changed')
        if result['status'] in {'audited','rejected'} and (value.get('binding_sha256') is None or value.get('process_sha256') is None):
            raise ValueError('Audited receipt has no exact object and process original')
        result_path = directory / 'adapter-result.json'
        if c.digest(c.regular(result_path)) != value['result_sha256'] or c.read(result_path) != value['result']:
            raise ValueError('Candidate audit adapter result changed')
    elif value['result']['status'] != 'unavailable':
        raise ValueError('An unaccepted audit cannot acquire qualification')
    if (value['result']['status'] not in {'audited', 'rejected', 'engineering_error', 'unclassified', 'unknown', 'unavailable'}
            or type(value['result']['cleanup_confirmed']) is not bool):
        raise ValueError('Candidate audit outcome fields changed')
    return value


def intents(root):
    """Enumerate accepted declarations without reading execution results."""
    for path in sorted((root/'jobs').glob('*.json')):
        file = _directory(root,int(path.stem))/'intent.json'
        if file.exists():
            intent = c.read(c.regular(file))
            validate_intent(root,intent)
            yield intent


def facts(root):
    """Result facts remain separate from accepted intent accounting."""
    for intent in intents(root):
        yield intent,_outcome(root,intent['decision']['step'])


def blocked(root):
    return any(outcome is None or not outcome['result']['cleanup_confirmed'] for _, outcome in facts(root))


def receipt_ref(root, step):
    value = _outcome(root, step)
    if value is None:
        return None
    path = _directory(root, step) / 'outcome.json'
    return dict(step=step, path=path.relative_to(root).as_posix(), sha256=c.digest(path))


@c.verified_digests()
def public_receipt(root, ref):
    expected = receipt_ref(root, ref['step'])
    if expected != ref:
        raise ValueError('Frozen candidate audit receipt changed')
    value = _outcome(root, ref['step'])
    result = value['result']
    return dict(candidate_id=value['candidate_id'], status=result['status'],
                note=result['note'], scope=SCOPE,**(dict(diagnostic=result['diagnostic']) if 'diagnostic' in result else {}),
                qualification=('passed' if result['status'] == 'audited' else 'rejected' if result['status'] == 'rejected' else 'not_completed'),
                artifact_scope='exact frozen artifact; does not establish target performance')


def _settle_legacy_preflight(root,intent):
    """Settle c5790ba's impossible-to-dispatch boundary, never replay its intent.

    Its old closeout guard necessarily raised before staging or Popen for this
    expanded source set. Absence of a process record alone is never sufficient.
    New calls additionally freeze dispatch.json before entering the adapter.
    """
    job=c.read(c.regular(root/'jobs'/f'{intent["decision"]["step"]:03d}.json'))
    directory=_directory(root,job['step'])
    begin=budget.closeout_state(root)['begin']
    if (job.get('purpose')!='closeout' or begin is None
            or job['delivery_sources']==begin['delivery_sources']
            or not {'intent.json','sources.json','binding.json'} <= {p.name for p in directory.iterdir()}
            or any(p.name not in {'intent.json','sources.json','binding.json','adapter-result.json'} for p in directory.iterdir())
            or (root/'host-delivery/audit-recovery'/intent['operation_id']).exists()
            or any(delivery.leaf_output(root).glob(intent['operation_id']+'-*'))):
        return False
    binding=c.read(c.regular(directory/'binding.json'))
    validate_binding(root,dict(intent,binding=binding))
    result=dict(status='unavailable',note='candidate_audit_legacy_preflight_not_dispatched',cleanup_confirmed=True,
        operation_id=intent['operation_id'],candidate_id=intent['candidate_id'],
        artifact_sha256=binding['source']['objects']['artifact']['sha256'])
    result_path=directory/'adapter-result.json'
    if result_path.exists():
        if c.read(c.regular(result_path))!=result:raise ValueError('Candidate audit preflight recovery changed')
    else:_save(result_path,result)
    _save(directory/'outcome.json',dict(decision=intent['decision'],candidate_id=intent['candidate_id'],accepted=True,
        result=result,intent_sha256=c.digest(directory/'intent.json'),binding_sha256=c.digest(directory/'binding.json'),
        result_sha256=c.digest(result_path),process_sha256=None,leaf_output=[]))
    return True


def _unavailable_input(root,job,chosen,rows):
    """Identity of the exact candidate's sealed originals, independent of later A nodes."""
    from . import history
    sources=[]
    for receipt in job['history']['nodes']:
        _,_,seal,seal_sha=history._source(root,receipt['step'])
        observed=seal.get('observations')
        if observed is not None and any(row['id']==chosen for row in c.read(root/observed['path'])['candidates']):
            sources.append(dict(step=receipt['step'],seal_sha256=seal_sha,observation=observed))
    return hashlib.sha256(delivery._bytes(dict(settings_sha256=c.digest(root/'settings.json'),
        candidate_id=chosen,sources=sources,unavailable=rows))).hexdigest()


def execute(root, job, payload):
    """Returns a blocking status, or None once an immutable receipt is available."""
    from .protocol import require_current
    require_current(root,job)
    step, chosen = job['step'], payload.get('candidate_recommendation')
    if job!=c.read(c.regular(root/'jobs'/f'{step:03d}.json')):
        raise ValueError('Candidate audit scientific task changed')
    directory = _directory(root, step)
    previous = _outcome(root, step)
    if previous is not None:
        if previous['result']['note']=='candidate_audit_unchanged_originals_unavailable':return 'candidate_audit_originals_unavailable'
        return None if previous['result']['cleanup_confirmed'] else 'candidate_audit_unknown'
    if (directory / 'intent.json').exists():
        if _settle_legacy_preflight(root,c.read(c.regular(directory/'intent.json'))):
            return None
        return 'candidate_audit_unknown'  # Never repeat an uncertain accepted call.
    if (root / 'pause.request').exists():
        return 'paused'
    from . import correction
    if correction.gate(root, step) is not None:
        return 'correction_paused'
    decision = _decision(root, step)
    intent = None
    binding = None
    resolved = None
    unavailable_input=None
    def finish(status, note, *, cleanup=True, result=None):
        result = result or dict(status=status, note=note, cleanup_confirmed=cleanup)
        if intent is not None and 'operation_id' not in result:
            result.update(operation_id=intent['operation_id'], candidate_id=chosen,
                          artifact_sha256=binding['source']['objects']['artifact']['sha256'] if binding else None)
        value = dict(decision=decision, candidate_id=chosen, accepted=intent is not None, result=result)
        if unavailable_input is not None:value['unavailable_input_sha256']=unavailable_input
        if intent is not None:
            _save(directory / 'adapter-result.json', result)
            value.update(intent_sha256=c.digest(directory / 'intent.json'),
                         binding_sha256=c.digest(directory / 'binding.json') if binding is not None else None,
                         result_sha256=c.digest(directory / 'adapter-result.json'),
                         process_sha256=c.digest(directory / 'process.json') if (directory / 'process.json').exists() else None,
                         leaf_output=(delivery.leaf_refs(root, resolved) if resolved else []) + delivery.leaf_refs(root, intent['operation_id']))
        _save(directory / 'outcome.json', value)
        if note=='candidate_audit_unchanged_originals_unavailable':return 'candidate_audit_originals_unavailable'
        return None if cleanup else 'candidate_audit_unknown'
    if not enabled(configuration_updates.for_job(root, job)):
        return finish('unavailable', 'candidate_audit_disabled')
    if chosen is None or chosen not in job['history']['candidate_ids']:
        return finish('unavailable', 'candidate_unavailable')
    policy = configuration_updates.for_job(root, job).get('delivery_policy')
    if policy is None or policy['adapter'] is None:
        return finish('unavailable', 'adapter_unavailable')
    if budget.execution_blocked(root):
        return 'candidate_audit_unknown'
    meter, limits = native.usage(root), budget.snapshot(root)
    settings = configuration_updates.for_job(root, job)
    reserve = budget.delivery_reserve(settings)
    soft_closeout = budget.node_mode(root) == 'soft' and job.get('purpose') == 'closeout'
    if soft_closeout:
        reserve = budget.closeout_floor(settings, preparation_seconds=budget.closeout_state(root)['begin']['preparation_seconds'])
        reserve['tokens'] += policy['closeout_a']['max_observed_tokens']
        reserve['seconds'] += policy['closeout_a']['max_seconds']
        reserve['steps'] += 1  # One actual follow-up A, plus the external actions.
    if meter.get('blocking_unknown_steps',meter['unknown_steps']):
        return 'candidate_audit_usage_unknown'
    seconds_unavailable = (limits['seconds_remaining'] <= policy['submit_seconds'] + reserve['seconds'] if soft_closeout else
        limits['seconds_remaining'] < policy['submit_seconds'] + reserve['seconds'])
    tokens_unavailable = (limits['max_tokens'] - meter.get('accounted_tokens',meter['observed_tokens']) <= reserve['tokens'] if soft_closeout else
        limits['max_tokens'] - meter.get('accounted_tokens',meter['observed_tokens']) < reserve['tokens'])
    if (seconds_unavailable or tokens_unavailable
            or budget.used_steps(root) + 1 + reserve['steps'] > budget.effective_max_steps(root)):
        return finish('unavailable', 'candidate_audit_reserve_unavailable')
    operation = _operation(root, decision, chosen)
    intent = dict(action_id=operation, action_kind='audit', operation_id=operation, decision=decision,
                  candidate_id=chosen, max_seconds=policy['submit_seconds'], advance_seconds=budget.snapshot(root)['advance_seconds'])
    _validate_request(root,intent)
    require_eligibility(root,intent)
    budget.transition(root, 'advance', 'candidate_audit')
    intent['advance_seconds']=budget.snapshot(root)['advance_seconds']
    _save(directory / 'intent.json', intent)
    try:
        if not job.get('delivery_sources'):resolved = hashlib.sha256(delivery._bytes(job['history']['nodes'])).hexdigest()
        refs = (delivery.selected_sources(root, job['delivery_sources'], chosen, prepare=True, job=job)
            if job.get('delivery_sources') else delivery.freeze_sources(root, job['history']['nodes'], candidate_id=chosen, job=job))
        rows = [row for row in refs['items'] if row['candidate_id'] == chosen and row['status'] == 'audit_pending']
        if len(rows)!=1:
            _save(directory/'sources.json',refs)
            failed=[row for row in refs['items'] if row['candidate_id']==chosen]
            unavailable_input=_unavailable_input(root,job,chosen,failed)
            unchanged=False
            for previous_job in sorted((root/'jobs').glob('*.json')):
                prior_step=int(previous_job.stem)
                if prior_step>=step:break
                old=_outcome(root,prior_step)
                if (old is not None and old['result']['status']=='unavailable'
                        and old.get('unavailable_input_sha256')==unavailable_input):unchanged=True;break
            note='candidate_audit_unchanged_originals_unavailable' if unchanged else 'candidate_originals_unavailable'
            detail=next((row.get('reason') for row in failed if row.get('reason')),'delivery_source_unavailable')
            return finish('unavailable',note,result=dict(status='unavailable',note=note,cleanup_confirmed=True,
                diagnostic='Exact candidate originals unavailable: '+detail+'. Repair the original binding or choose another candidate; unchanged inputs are not retried.'))
        binding = dict(candidate_id=chosen, decision=decision, source=rows[0], target=policy['adapter']['target'], operation_id=operation)
        _validate_objects(root,intent,binding,refs)
        _save(directory / 'sources.json', refs)
        _save(directory / 'binding.json', binding)
        def process(phase, identity):
            _save(directory / 'process.json', dict(operation_id=operation, phase=phase, identity=identity))
        _save(directory / 'dispatch.json',_dispatch_binding(root,intent))
        result = delivery.invoke(root, dict(intent, binding=binding), policy['submit_seconds'],
                                 on_process=process, audit_only=True)
        return finish(result['status'], result['note'], cleanup=result['cleanup_confirmed'], result=result)
    except delivery.DeliveryUnresolved as error:
        return finish('unknown', error.code, cleanup=error.code != 'delivery_cleanup_unknown')


def collect_refs(root, sources, *, step, adjacent=None):
    """Only frozen refs witnessed by the verified prefix, plus this exact next A."""
    refs = {}
    values = []
    steps = {job['step'] for job, _, _, _ in sources}
    for job, _, _, _ in sources:
        ref = job.get('candidate_audit')
        if ref is not None:
            if job['role'] != 'A' or ref['step'] != job['step'] - 1:
                raise ValueError('Historical audit lacks its exact adjacent sealed anchor')
            values.append(ref)
    if adjacent is not None:
        if adjacent['step'] != step - 1 or adjacent['step'] not in steps:
            raise ValueError('Adjacent audit is outside the frozen scientific prefix')
        values.append(adjacent)
    for ref in values:
        if not 0 < ref['step'] < step:
            raise ValueError('Audit receipt is outside the task boundary')
        public_receipt(root, ref)  # Original outcome/binding/process, not scientific prose.
        if ref['step'] in refs and refs[ref['step']] != ref:
            raise ValueError('Conflicting original audit receipts')
        refs[ref['step']] = ref
    return [refs[key] for key in sorted(refs)]


def candidate_artifacts(root, records):
    """Exact candidate declarations mapped to their archived artifact identities."""
    models = {}
    for record in records:
        observation = record.get('observation') or {}
        for context in observation.get('contexts',[]):
            if context.get('status')!='known_success':continue
            from . import lineage
            for artifact in lineage.context_artifacts(context):
                if artifact.get('retention')=='archived':
                    models.setdefault(context['candidate_id'],set()).add((artifact['sha256'],artifact['size_bytes']))
        from . import generic_results
        # Candidate identities and bytes come from their exact frozen declaration.
        for call in observation.get('managed_calls',[]):
            for candidate in generic_results.candidates([call]):
                artifacts=[item for item in candidate['evidence'] if item.get('name') is not None]
                for artifact in artifacts:
                    if artifact.get('retention')=='archived':
                        identity=candidate['id']
                        if identity not in {row['id'] for row in observation.get('candidates',[])}:
                            from . import candidate_refs,originals
                            source=originals.node(root,record['step'])
                            identity=candidate_refs.origin_candidate(source.origin_identity,identity)
                        models.setdefault(identity,set()).add((artifact['sha256'],artifact['size_bytes']))
        actual = observation.get('actual_submission')
        if actual and actual.get('candidate_id') is not None and (actual.get('artifact') or {}).get('retention') == 'archived':
            model = actual['artifact']
            models.setdefault(actual['candidate_id'], set()).add((model['sha256'], model['size_bytes']))
    return models


def project_receipts(root, records, refs):
    """Adapter results and candidate-byte attribution; no leakage interpretation."""
    models=candidate_artifacts(root,records)
    rows = []
    for ref in refs:
        public_receipt(root, ref)
        value = _outcome(root, ref['step'])
        result, chosen = value['result'], value['candidate_id']
        binding = None
        if value.get('binding_sha256') is not None:
            binding = c.read(c.regular(_directory(root, ref['step']) / 'binding.json'))
        artifact = binding['source']['objects']['artifact'] if binding is not None else None
        identities = models.get(chosen, set())
        match = ('unknown' if artifact is None or not identities else
                 'matched' if identities == {(artifact['sha256'], artifact['size_bytes'])} else 'different')
        spec = (configuration_updates.for_step(root, ref['step']).get('delivery_policy') or {}).get('adapter')
        label = Path(spec['adapter']).name if spec is not None else 'unavailable'
        rows.append(dict(candidate_id=chosen, adapter_result=result['status'], artifact_binding=match,
                         audit_step=ref['step'], adapter=label.encode()[:80].decode(errors='ignore'),
                         scope=SCOPE, qualification='passed' if result['status'] == 'audited' and match == 'matched' else
                         'artifact_scope_unknown' if result['status'] == 'audited' else 'rejected' if result['status']=='rejected' else 'not_completed'))
    return rows
