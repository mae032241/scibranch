"""Structural W delivery checks from frozen observations; A owns their meaning."""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import configuration_updates
from . import budget, contracts as c, history, execution
from .output_repair import WorkRepairRequired

FACT_NAME = 'work-evidence.json'


def schema():
    return dict(type=['array','null'],items=dict(type='object',additionalProperties=False,
        properties=dict(mechanism=dict(type='string'),
            basis=dict(type='string',enum=['observed','blocked']),
            finding=dict(type='string')),required=['mechanism','basis','finding']),
        description='For a screen task, give your own scientific finding and limitations for each assigned mechanism. '
            'observed uses exact available observations; blocked states a concrete scientific obstacle and goes to A as partial. '
            'Names are scientific labels or listed mechanism refs. Do not write hashes, call IDs, paths or counts. Local tasks may use null.')


def applicable(job):
    return job['role']=='W' and ((job.get('search') or {}).get('focus') or {}).get('action')=='screen'


def _bytes(value):
    return (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()


def _original(root,path):
    return history._proof(root,Path(path).relative_to(root).as_posix())


def _candidate(root,accepted,completed,terminal,observation=None):
    """No live backend query; terminal bytes must agree with the accepted owner."""
    if observation is not None:return history.managed_status(root,observation) in {'known_success','known_failed'}
    if history._execution_interface(root,accepted).get('observation_version') is not None:return False
    if not ('local_observation_version' in accepted or 'receipt_transport' in accepted):return False
    from . import experiments
    return experiments.local_terminal_status(accepted,completed,terminal) in {'known_success','known_failed'}



def writers_stopped(root,job,record):
    from . import runtime
    number=execution.records(root,job['step']).index(record)
    identity=execution.identity(root,job['step'],number)
    from . import worker_execution_bridge
    worker_execution_bridge.stopped(record,identity)
    for name in ('runner-process.json','native-process.json'):
        path=record/name
        if path.exists():
            receipt=c.read(c.regular(path))
            if name=='runner-process.json' and receipt.get('pid')==os.getpid():
                outcome=c.read(c.regular(record/'native-outcome.json'))
                if c.record_ref(path)==outcome.get('execution_processes',{}).get(name) and receipt.get('task_ref')==identity:
                    continue  # The original leased owner is publishing its final checkpoint.
            if not runtime.process_matches(receipt,identity):continue
            raise ValueError('Original native writer still owns this attempt; no delivery snapshot can be captured')


def _prefix_calls(root,job):
    """Resolve only this task's frozen predecessors and recovery prefix."""
    from . import originals,execution_recovery
    recovered=execution_recovery.resolved(root,originals.recovery_refs(originals.node(root,job['step']),job))
    for receipt in job['history']['nodes']:
        source=originals.node(root,receipt['step'])
        seal_path=originals.source_record(source,'result-identity.json')
        if history.source_seal_ref(source)!=receipt['seal_ref']:
            raise history.ObserverIntegrityError('Worker evidence prefix changed')
        seal=c.read(seal_path);owner=originals.source_job(source)
        if seal.get('observations') is None:continue
        observed=(history.validate_observation_original(source,owner,seal) if source.inherited
            else history.read_observation(root,owner,seal))
        if (owner.get('search') or {}).get('breadth'):
            history.validate_managed(root,owner,observed['managed_calls'])
        for row in observed.get('managed_calls',[]):
            row=recovered.get((source.step,row['call_id']),row)
            if source.inherited:history.validate_managed_original(source,owner,[row])
            yield source,row


def _evidence(source,row):
    proofs={key:row[key] for key in ('accepted','completed','terminal')}
    if 'observation' in row:proofs['observation']=row['observation']
    if source.inherited:
        proofs={key:(dict(proof,origin_identity=source.origin_identity) if proof is not None else None)
            for key,proof in proofs.items()}
    for key in ('originals','outputs'):
        proofs[key]=[dict(item,origin_identity=source.origin_identity) if source.inherited else dict(item)
                     for item in row.get(key,[])]
    return dict(mechanism_ref=row['mechanism_binding']['mechanism_ref'],**proofs)


def accepted_original(root,proof,*,source=None):
    from . import originals
    ref=dict(proof,path=proof['snapshot'])
    owner=originals.source_for_ref(source,ref) if source is not None else None
    return c.read(originals.resolve_ref(owner,ref) if owner is not None else originals.resolve_root_ref(root,ref))


def capture(root,job,record):
    """The native owner calls this only after its attempt has stopped writing.

    This is one attempt's fact original, not a new call index or mutable balance.
    Each proof uses the existing archive. Later calls/collections cannot change it.
    """
    if not applicable(job):return None
    from .protocol import require_current,stage_scope
    require_current(root,job)
    root,record=Path(root),Path(record);path=record/FACT_NAME
    number=execution.records(root,job['step']).index(record)
    binding=dict(job_ref=c.record_ref(root/'jobs'/f'{job["step"]:03d}.json'),
        raw_ref=c.file_ref(c.regular(record/'science-output.json',limit=None)),source_attempt=number)
    if path.exists():return validate(root,job,record)
    writers_stopped(root,job,record)
    needed={row['mechanism_ref'] for row in job['search']['coverage_obligation']}
    rows=[];unknown=[];capacity=False
    # Frozen predecessor observations only, never recursively copy their assets.
    for source,row in _prefix_calls(root,job):
        if row.get('mechanism_binding') is None:continue
        ref=row['mechanism_binding']['mechanism_ref']
        if ref in needed and row['status'] in {'known_success','known_failed'}:rows.append(_evidence(source,row))
    for row in history.managed_calls(root,job):
        accepted=c.read(root/row['accepted']['snapshot'])
        attempt=accepted.get('native_attempt')
        if not isinstance(attempt,dict) or type(attempt.get('number')) is not int:
            raise history.ObserverIntegrityError('Worker observation has no exact accepted native attempt')
        if attempt['number']>number:continue
        proofs={key:row[key] for key in ('accepted','completed','terminal')}
        if 'observation' in row:proofs['observation']=row['observation']
        proofs.update(originals=row.get('originals',[]),outputs=row.get('outputs',[]))
        if row['status'] in {'known_success','known_failed'}:
            ref=accepted['mechanism_binding']['mechanism_ref']
            if ref in needed:rows.append(dict(mechanism_ref=ref,**proofs))
        elif row['status']=='unknown':unknown.append(proofs)
        # A verified permanent non-submission is neither an observation nor
        # unknown work. Its complete proof stays with the history owner.
    # Keep one exact observation per required current-H mechanism.
    obligations={row['mechanism_ref']:row for row in job['search']['coverage_obligation']};selected={}
    for row in rows:
        original=row['accepted']
        if original.get('retention')!='archived':capacity=True;continue
        value=accepted_original(root,original);mechanism=value.get('mechanism_binding') or {};ref=row['mechanism_ref'];expected=obligations[ref]
        if all(mechanism.get(key)==expected[key] for key in ('mechanism_ref','source_h','source_h_ref')):selected[ref]=row
    value=dict(version=1,binding=binding,evidence=list(selected.values()),unknown=unknown,
        prior_unknown=job['search']['breadth']['prior_unknown'],capacity=capacity,omitted_evidence=0,omitted_unknown=0)
    c.write(path,value);path.chmod(0o400)
    return validate(root,job,record)


def validate(root,job,record):
    value=c.read(c.regular(record/FACT_NAME,limit=None))
    expected=dict(job_ref=c.record_ref(root/'jobs'/f'{job["step"]:03d}.json'),
        raw_ref=c.file_ref(c.regular(record/'science-output.json',limit=None)),
        source_attempt=execution.records(root,job['step']).index(record))
    if value.get('version')!=1 or value.get('binding')!=expected:raise history.ObserverIntegrityError('Worker fact snapshot binding changed')
    inherited=None
    for row in [*value['evidence'],*value['unknown']]:
        for key in ('accepted','completed','terminal','observation'):
            proof=row.get(key)
            if proof is not None:history._validate_proof(root,proof)
        if 'mechanism_ref' in row:
            if any(row[key] is None or row[key].get('retention')!='archived' for key in ('accepted','completed')):
                raise history.ObserverIntegrityError('Worker known observation originals unavailable')
            accepted=accepted_original(root,row['accepted'])
            if 'origin_identity' in row['accepted']:
                if inherited is None:
                    inherited=[_evidence(source,call) for source,call in _prefix_calls(root,job)
                        if source.inherited and call.get('mechanism_binding') is not None
                        and call['status'] in {'known_success','known_failed'}]
                if row not in inherited:
                    raise history.ObserverIntegrityError('Worker evidence lacks its exact frozen inherited observation')
            elif history.managed_status(root,row) not in {'known_success','known_failed'}:
                raise history.ObserverIntegrityError('Worker observation differs from accepted terminal originals')
            ref=row['mechanism_ref'];target=next((item for item in job['search']['coverage_obligation'] if item['mechanism_ref']==ref),None)
            if target is None or any(accepted['mechanism_binding'].get(key)!=target[key] for key in ('mechanism_ref','source_h','source_h_ref')):
                raise history.ObserverIntegrityError('Worker observation differs from assigned H version')
    return value


def assessment(root,job,science,record):
    """Coverage of this task's own authored findings; no scientific screened verdict."""
    if not applicable(job):return dict(status='not_applicable',missing=[],blocked=[],reason=None)
    facts=validate(root,job,record)
    expected={row['mechanism_ref'] for row in job['search']['coverage_obligation']}
    mechanisms=job['search']['breadth']['mechanisms'];known={row['mechanism_ref'] for row in facts['evidence']}
    declared=c.scientific_field(job,science,'mechanism_findings')
    unresolved=science.get('mechanism_findings') is not None and declared is None
    findings=declared or [];seen=set();missing=[];blocked=[]
    for row in findings:
        matches=[item['mechanism_ref'] for item in mechanisms if row['mechanism'] in (item['mechanism_ref'],item['label'])]
        if len(matches)!=1 or matches[0] not in expected or matches[0] in seen or not row['finding'].strip():
            missing.append(row['mechanism']);continue
        ref=matches[0];seen.add(ref)
        if row['basis']=='blocked':blocked.append(ref)
        elif ref not in known:missing.append(ref)
    missing=sorted(set(missing)|(expected-seen))
    reason=('work_evidence_capacity' if facts['capacity'] else 'original_execution_unknown' if facts['unknown'] or facts['prior_unknown']
        else 'scientific_findings_unresolved' if unresolved else 'worker_decision' if science.get('stage_intent')=='decision'
        else 'scientific_blocked' if blocked else None)
    return dict(status='partial' if reason else 'incomplete' if missing else 'complete',missing=missing,blocked=blocked,reason=reason,
        facts_ref=c.record_ref(c.regular(record/FACT_NAME)))


def require_delivery(root,job,science,record):
    result=assessment(root,job,science,record)
    if result['status']=='incomplete':
        ending=record/'stage-end.json'
        if ending.exists():
            from . import checkpoint
            value=checkpoint.stage_end(root,job,record)
            if value.get('work_exit') is not None:
                reason=validate_exit(root,job,record,value['work_exit'])
                if value['reason']!=reason:raise history.ObserverIntegrityError('Stage ending changed its verified Worker exception')
                return dict(result,status='partial',reason=reason)
        raise WorkRepairRequired('Complete your scientific findings for assigned mechanisms: '+', '.join(result['missing'])+'. Reuse retained observations; do not resubmit completed experiments.')
    return result


def authorize_exit(root,job,record,reason):
    """Freeze a host exception from original policy/receipts, never a Worker hint."""
    from . import budget,continuation,experiments,native
    paths=execution.records(root,job['step']);number=paths.index(record)
    value=dict(reason=reason,job_ref=c.record_ref(root/'jobs'/f'{job["step"]:03d}.json'),
        source_attempt=number,outcome_ref=c.record_ref(c.regular(record/'native-outcome.json')))
    policy=configuration_updates.for_job(root, job).get('resume_policy')
    repairs=sum(c.read(path/'native-intent.json').get('continuation_kind','repair')=='repair' for path in paths[1:number+1])
    if (reason=='worker_resume_unsupported' and policy is None
            or reason=='worker_resume_output_only' and policy is not None and policy.get('output_only') is True):
        return dict(value,kind='resume_policy',settings_ref=c.record_ref(root/'settings.json'))
    if reason=='worker_resume_limit' and policy is not None and repairs>=policy['max_attempts']:
        return dict(value,kind='repair_cap',max_attempts=policy['max_attempts'])
    if reason.startswith('worker_resume_') and reason not in {'worker_resume_terminal_ineligible','worker_resume_original_running','worker_resume_usage_unknown'}:
        try:continuation.session(root,job['step'],record)
        except continuation.ResumeUnavailable as error:
            if str(error)==reason:return dict(value,kind='session',native_jsonl=_original(root,record/'native.jsonl'))
    admission=budget.review_configuration(root,job)
    if admission['mode']=='soft':
        sample=budget.clock_stamp()
        original=_original(root,Path(admission['directory'])/'review.json')
        review=experiments._review_observation(c.read(root/original['snapshot']),sample)
        if review['requested'] and review['enforced'] and review['reason']==reason:
            accepted=dict(value,kind='host_review',review=original,sample=sample)
            validate_exit(root,job,record,accepted)
            return accepted
    if reason in {'worker_resume_node_exhausted','worker_resume_budget_unavailable'}:
        try:budget.accept_continuation(root,job,paths[:number+1])
        except budget.NodeBudgetUnavailable as error:
            if error.reason==reason:
                tokens,seconds=budget._attempt_consumption(root,job,paths[:number+1])
                return dict(value,kind='budget',consumption=dict(tokens=tokens,seconds=seconds),
                    limits=budget.node_limits(root,job),shared=dict(budget.snapshot(root),used_steps=budget.used_steps(root)),meter=native.usage(root))
    raise ValueError('No original host exception authorizes incomplete W delivery: '+reason)


def validate_exit(root,job,record,value):
    """A hand-written stage-end reason is insufficient to bypass ordinary review."""
    from . import budget
    number=execution.records(root,job['step']).index(record)
    if (not isinstance(value,dict) or value.get('job_ref')!=c.record_ref(root/'jobs'/f'{job["step"]:03d}.json')
            or value.get('source_attempt')!=number or value.get('outcome_ref')!=c.record_ref(c.regular(record/'native-outcome.json'))):
        raise history.ObserverIntegrityError('Worker exit differs from original attempt')
    policy=configuration_updates.for_job(root, job).get('resume_policy');kind=value.get('kind');reason=value['reason']
    if kind=='resume_policy':
        unsupported=reason=='worker_resume_unsupported' and policy is None
        output_only=reason=='worker_resume_output_only' and policy is not None and policy.get('output_only') is True
        if not (unsupported or output_only) or value.get('settings_ref')!=c.record_ref(root/'settings.json'):
            raise history.ObserverIntegrityError('Worker exit has no original unsupported policy')
    elif kind=='repair_cap':
        paths=execution.records(root,job['step'])[:number+1]
        repairs=sum(c.read(path/'native-intent.json').get('continuation_kind','repair')=='repair' for path in paths[1:])
        if policy is None or reason!='worker_resume_limit' or value['max_attempts']!=policy['max_attempts'] or repairs<policy['max_attempts']:
            raise history.ObserverIntegrityError('Worker exit has no exhausted original repair cap')
    elif kind=='session':
        from . import continuation
        history._validate_proof(root,value['native_jsonl'])
        if value['native_jsonl']['path']!=(record/'native.jsonl').relative_to(root).as_posix():raise history.ObserverIntegrityError('Worker unavailable session source changed')
        try:continuation.session(root,job['step'],record)
        except continuation.ResumeUnavailable as error:
            if str(error)!=reason:raise history.ObserverIntegrityError('Worker unavailable session diagnosis changed') from error
        else:raise history.ObserverIntegrityError('Original session was available; incomplete delivery cannot bypass correction')
    elif kind=='host_review':
        from . import experiments
        proof=value['review'];history._validate_proof(root,proof)
        expected=(budget._review_directory(root,job)/'review.json').relative_to(root).as_posix()
        if proof.get('path',)!=expected or proof.get('retention')!='archived':raise history.ObserverIntegrityError('Worker exit did not use protected host review')
        review=c.read(root/proof['snapshot']);sample=value['sample']
        from .protocol import stage_scope
        if (review['scope']!=stage_scope(root,job) or review['stage_anchor']!=c.record_ref(root/'jobs'/f'{job["step"]:03d}.json')
                or review['native_attempt']!=dict(number=number,execution_key=execution.identity(root,job['step'],number))
                or sample['boot_id']!=review['boot_id']):raise history.ObserverIntegrityError('Worker host review scope changed')
        actual=experiments._review_observation(review,sample)
        if not actual['requested'] or not actual['enforced'] or actual['reason']!=reason:
            raise history.ObserverIntegrityError('Worker host review did not require this exit')
    elif kind=='budget':
        paths=execution.records(root,job['step'])[:number+1]
        tokens,seconds=budget._attempt_consumption(root,job,paths)
        if value['consumption']!=dict(tokens=tokens,seconds=seconds) or value['limits']!=budget.node_limits(root,job):raise history.ObserverIntegrityError('Worker budget exit refunded original consumption')
        if reason=='worker_resume_node_exhausted':
            if budget.node_mode(root)!='hard' or tokens<value['limits']['max_observed_tokens'] and seconds<value['limits']['max_seconds']:
                raise history.ObserverIntegrityError('Worker node budget was not exhausted')
        elif reason=='worker_resume_budget_unavailable':
            # The control snapshot is immutable in stage-end; source consumption
            # and policy are checked above. Later amendments never rewrite it.
            shared=value['shared'];meter=value['meter'];soft=budget.node_mode(root)=='soft'
            reserve=budget.delivery_reserve(configuration_updates.for_job(root, job)) or dict(tokens=0,seconds=0,steps=0)
            actual_only=configuration_updates.for_job(root, job).get('final_a_completion_policy') is not None
            needed_tokens=0 if soft or actual_only else max(0,value['limits']['max_observed_tokens']-tokens)
            needed_seconds=0. if soft else max(0.,value['limits']['max_seconds']-seconds)
            if not (meter.get('blocking_unknown_steps', meter['unknown_steps']) or shared['max_tokens']-meter.get('accounted_tokens', meter['observed_tokens'])<needed_tokens+reserve['tokens']
                    or shared['seconds_remaining']<needed_seconds+reserve['seconds']
                    or (soft or actual_only) and shared['max_tokens']-meter.get('accounted_tokens', meter['observed_tokens'])<=reserve['tokens']
                    or soft and shared['seconds_remaining']<=reserve['seconds']
                    or budget.effective_max_steps(root)-shared.get('used_steps',budget.used_steps(root))<1+reserve['steps']):
                raise history.ObserverIntegrityError('Worker shared budget did not require exit')
        else:raise history.ObserverIntegrityError('Unknown Worker budget exception')
    else:raise history.ObserverIntegrityError('Worker exit lacks original host exception facts')
    return reason
