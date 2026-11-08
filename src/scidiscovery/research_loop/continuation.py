"""Explicit finite continuation of a known local failure, never a fresh replay."""
import json
import os
import time
from pathlib import Path

from . import configuration_updates
from . import budget, budget_limits, contracts as c, execution, native, runtime, checkpoint
from .output_repair import OutputRepairRequired, WorkRepairRequired


class ResumeUnavailable(ValueError):
    pass


def consultation_binding(root, job, *, parents):
    """Bind ordinary advice to its exact W from the producer's parent facts."""
    if (job.get('worker_continuity_version')!=1 or job.get('role')!='W'
            or any(job.get(key) for key in ('purpose','formal_review','final_only','final_report',
                'engineering_repair','author_correction','correction_request','evidence_return'))
            or job['step']-1 not in parents):
        return None
    advice=parents[job['step']-1]
    consultant=advice['job']
    if (consultant.get('role') not in {'H','A'} or consultant.get('formal_review')
            or any(consultant.get(key) for key in ('final_only','final_report',
                'engineering_repair','author_correction','correction_request'))
            or consultant.get('purpose') not in {None,'worker_consultation'}
            or (consultant.get('evidence_return') or {}).get('kind') is not None):
        return None
    requester=(consultant.get('evidence_return') or {}).get('step')
    if consultant.get('purpose')!='worker_consultation':
        if consultant['role']!='A' or advice['payload'].get('next_action')!='continue_w':
            return None
        workers=[step for step,facts in parents.items() if facts['job']['role']=='W'
            and any(ref['path']==f'inputs/parents/{step:03d}/conclusion.json'
                for ref in consultant.get('direct_refs',[]))]
        requester=max(workers,default=None)
    if requester not in parents or parents[requester]['job']['role']!='W':
        return None
    source=parents[requester]
    return dict(version=1,requester_step=requester,advice_step=consultant['step'],
        requester_job=source['seal']['job_ref'],advice_job=advice['seal']['job_ref'],
        requester_seal=dict(path=str(Path(source['seal']['job_ref']['path']).parents[1]/
            'native-evidence'/f'{requester:03d}'/'result-identity.json')),
        advice_seal=dict(path=str(Path(advice['seal']['job_ref']['path']).parents[1]/
            'native-evidence'/f'{consultant["step"]:03d}'/'result-identity.json')),
        requester_native=dict(path=str(Path(source['seal']['raw_ref']['path']).parent/'native-intent.json')))


def consultation_launch(root, job, *, settings, backend, profile):
    """Produce one backend-qualified initial resume, or an honest fresh context.

    Source originals stay at their original workspace. The selected backend's
    resume transport must independently verify the successor invocation cwd,
    model and permissions before it submits the new turn.
    """
    binding=job.get('worker_continuation')
    if binding is None:return {}
    source,advice=binding.get('requester_step'),binding.get('advice_step')
    if (job.get('worker_continuity_version')!=1 or job.get('role')!='W'
            or type(source) is not int or type(advice) is not int or not 0<source<advice<job['step']
            or advice!=job['step']-1 or binding.get('version')!=1
            or any(not any(ref['path']==f'inputs/parents/{step:03d}/conclusion.json'
                for ref in job.get('direct_refs',[])) for step in (source,advice))):
        raise ValueError('Worker consultation continuation changed its exact originals')
    public=dict(version=1,requester_step=source,advice_step=advice)
    def fresh(reason):
        return dict(context_mode='new_context',worker_continuation=dict(public,
            mode='new_context',fallback_reason=reason))
    if (binding.get('requester_job')!=dict(path=str(root/'jobs'/f'{source:03d}.json'))
            or binding.get('advice_job')!=dict(path=str(root/'jobs'/f'{advice:03d}.json'))):
        return fresh('consultation_resume_inherited_source')
    if (binding.get('requester_seal')!=dict(path=str(execution.origin(root,source)/'result-identity.json'))
            or binding.get('advice_seal')!=dict(path=str(execution.origin(root,advice)/'result-identity.json'))):
        raise ValueError('Worker consultation source seal locator changed')
    if backend.recovery_capability('consultation_resume').get('status')!='supported':
        return fresh('consultation_resume_unsupported_backend')
    seal=c.read(c.regular(Path(binding['requester_seal']['path'])))
    advisor=c.read(c.regular(Path(binding['advice_seal']['path'])))
    if seal.get('job_ref')!=binding['requester_job'] or advisor.get('job_ref')!=binding['advice_job']:
        raise ValueError('Worker consultation source seal changed')
    record=Path(seal['raw_ref']['path']).parent
    original_record=execution.origin(root,source)
    relative=record.relative_to(original_record)
    if (record.resolve()!=record or relative.parts and (len(relative.parts)!=2
            or relative.parts[0]!='continuations' or not relative.parts[1].isdecimal()
            or relative.parts[1]!=f'{int(relative.parts[1]):03d}')):
        raise ValueError('Worker consultation source native namespace changed')
    if binding.get('requester_native')!=c.record_ref(record/'native-intent.json'):
        raise ValueError('Worker consultation source attempt changed')
    original=c.read(c.regular(record/'native-intent.json'))
    outcome=c.native_outcome(record)
    if (original.get('task_ref')!=binding['requester_job'] or outcome.get('raw_ref')!={
            key:seal['raw_ref'][key] for key in ('path','size_bytes')}
            or outcome.get('returncode')!=0
            or outcome.get('stop_reason') or outcome.get('terminal_observed') is not True
            or outcome.get('cleanup')!='observed_group_cleaned'):
        raise ResumeUnavailable('consultation_resume_original_unverified')
    if outcome.get('native_backend')!=backend.name:
        # Configuration revisions may legally change the selected provider.
        # native_outcome interpreted its original provider above; that known
        # terminal is retained, but another provider cannot resume its session.
        return fresh('consultation_resume_backend_changed')
    if (seal.get('control_outcome') or {}).get('stage_end_reason') in {
            'worker_context_review_due','worker_context_observation_unknown'}:
        return fresh('consultation_resume_context_rotation_required')
    if original.get('native_profile')!=profile:
        return fresh('consultation_resume_profile_changed')
    native_identity=outcome.get('native_identity') or {}
    retained=native_identity.get('session')
    if not isinstance(retained,dict):return fresh('consultation_resume_session_unavailable')
    home=(settings.get('resume_policy') or {}).get('native_home')
    if (not (settings.get('resume_policy') or {}).get('output_only')
            or home is None or home!=retained.get('home') or home!=native_identity.get('home')):
        return fresh('consultation_resume_shared_home_unavailable')
    if retained.get('thread_id')!=outcome.get('thread_id'):
        raise ValueError('Worker consultation source thread changed')
    from .native_usage import MetadataUnavailable
    try:
        backend.validate_resume_session(retained,c.workspace(root,source),Path(home))
        backend.validate_session_event(outcome.get('native_thread_event'),outcome['thread_id'])
    except MetadataUnavailable:
        return fresh('consultation_resume_session_unavailable')
    return dict(context_mode='same_context',native_home=home,session=retained,
        worker_context_state=(outcome.get('metering') or {}).get('context_windows'),
        worker_continuation=dict(public,mode='resume_requested',fallback_reason=None))


def consultation_outcome(planned, *, resumed, verified):
    """Public invocation fact; a requested resume is not an observed success."""
    if planned is None:return None
    value=dict(planned)
    if value['mode']!='new_context':
        value['mode']='same_session' if resumed and verified else 'resume_unconfirmed'
    return value


def consultation_intent(job, intent, *, settings, backend):
    """Consume the accepted producer's plan; never reopen source originals."""
    binding=job.get('worker_continuation')
    public=intent.get('worker_continuation')
    if (not isinstance(binding,dict) or not isinstance(public,dict)
            or job.get('worker_continuity_version')!=1 or job.get('role')!='W'
            or public.get('version')!=1
            or any(public.get(key)!=binding.get(key) for key in ('requester_step','advice_step'))):
        raise ValueError('Accepted worker consultation plan changed')
    if public.get('mode')=='new_context':
        if (intent.get('context_mode')!='new_context' or intent.get('session') is not None
                or not isinstance(public.get('fallback_reason'),str) or not public['fallback_reason']):
            raise ValueError('Accepted worker consultation fallback changed')
        return dict(worker_continuation=public)
    session=intent.get('session')
    home=(settings.get('resume_policy') or {}).get('native_home')
    if (public.get('mode')!='resume_requested' or public.get('fallback_reason') is not None
            or intent.get('context_mode')!='same_context' or not isinstance(session,dict)
            or not (settings.get('resume_policy') or {}).get('output_only')
            or home is None or intent.get('native_home')!=home or session.get('home')!=home
            or backend.recovery_capability('consultation_resume').get('status')!='supported'):
        raise ValueError('Accepted worker consultation session changed')
    return dict(worker_continuation=public,session=session,
        worker_context_state=intent.get('worker_context_state'))


class AutomaticRecoveryRequired(RuntimeError):
    """Return one finite recovery decision to the owning controller writer."""
    def __init__(self, decision, *, inventory=None):
        super().__init__(decision['reason'])
        self.decision = decision
        self.inventory = inventory


def technical_stop_from_facts(job, outcome, *, recovery_available=False,
        blocked_reason=None, attempt=0):
    """Public control diagnosis from the owner's already-read originals."""
    from . import native_recovery
    if not native_recovery.never_started(outcome):return None
    value = dict(phase='native_prelaunch', stage='technical_pause',
        pending_step=job['step'], pending_role=job['role'],
        reason=outcome['stop_reason'], repair_scope='worker_attempt',
        recovery_available=bool(recovery_available), recheck_required=True,
        expected_attempt=attempt,
        supported_actions=['worker-resume'] if recovery_available else [])
    if blocked_reason is not None:value['blocked_reason'] = blocked_reason
    return value


def _prelaunch_original_facts(root, step, record, *, attempt):
    """Produce exact filesystem/process observations for one failed attempt."""
    from .native_usage import stream_binding
    binding = (c.record_ref(root/'jobs'/f'{step:03d}.json') if attempt == 0 else
        c.record_ref(record/'native-intent.json'))
    receipt = c.read(c.regular(record/'runner-process.json'))
    return dict(binding=binding,receipt=receipt,
        stream=stream_binding(record/'native.jsonl'),
        native_process_present=(record/'native-process.json').exists() or (record/'native-process.json').is_symlink(),
        running=runtime.process_matches(receipt,binding),
        unexpected_activity=any((record/name).exists() or (record/name).is_symlink()
            for name in ('usage.json','science-output.json','result.pending.json')))


def _require_never_started(outcome, facts):
    """Pure qualification of the exact stopped runner's produced facts."""
    from . import native_recovery
    if not native_recovery.never_started(outcome):
        raise ResumeUnavailable('technical_restart_original_ineligible')
    if facts['native_process_present']:
        raise ResumeUnavailable('technical_restart_native_process_present')
    if facts['stream'] != outcome['native_stream']:
        raise ValueError('Original native stream changed')
    receipt,binding=facts['receipt'],facts['binding']
    if (receipt.get('task_ref') != binding
            or not isinstance(receipt.get('boot_id'), str) or not receipt['boot_id']
            or any(type(receipt.get(key)) is not int or receipt[key] <= 0
                for key in ('pid','start_ticks','pgid','sid','ppid'))):
        raise ValueError('Original prelaunch runner identity changed')
    if facts['running']:
        raise ResumeUnavailable('worker_resume_original_running')
    if facts['unexpected_activity']:
        raise ResumeUnavailable('technical_restart_unexpected_activity')


def validate_technical_restart(root, job, intent, *, prior_paths, prior_intents, outcomes, original_facts):
    """A new attempt can replace only the same never-submitted task prefix."""
    number = intent.get('attempt')
    if (type(number) is not int or number < 1 or number != len(prior_paths)
            or intent.get('continuation_kind') != 'technical_restart'
            or len(prior_intents) != number or len(outcomes) != number or len(original_facts) != number
            or intent.get('technical_restart') != dict(source_attempt=number-1,
                outcome_ref=c.record_ref(prior_paths[-1]/'native-outcome.json'))
            or intent.get('session') is not None or intent.get('context_mode') != 'new_context'
            or intent.get('native_home') != str(root/'native-homes'/f'{job["step"]:03d}'/f'context-{number:03d}')
            or any(intent.get(key) is not None for key in
                ('automatic_recovery','output_repair','work_repair','execution_wake','interruption'))):
        raise ValueError('Technical restart changed its exact original scope')
    for index,(path,source,outcome) in enumerate(zip(prior_paths,prior_intents,outcomes)):
        if index and source.get('continuation_kind') != 'technical_restart':
            raise ValueError('Technical restart cannot replace previously submitted science')
        _require_never_started(outcome,original_facts[index])


def _technical_restart_gate(root, job, paths, intents, outcomes, *, head, settings):
    """Read current admission once, without accepting scientific output."""
    from . import backends, correction, fork_runtime
    if (execution.origin(root,job['step'])/'result-identity.json').exists():
        raise ResumeUnavailable('technical_restart_stage_already_sealed')
    if (root/'pause.request').exists() or head['closeout'] is not None or head['final_return'] is not None:
        raise ResumeUnavailable('worker_resume_paused_or_closeout')
    if correction.gate(root,job['step']-1) is not None:
        raise ResumeUnavailable('worker_resume_correction_paused')
    if (head.current_job is None or head.current_job['step'] != job['step']
            or head.current_job['job_ref'] != c.record_ref(root/'jobs'/f'{job["step"]:03d}.json')):
        raise ResumeUnavailable('technical_restart_current_job_changed')
    if (head['active_audit'] is not None or head['unresolved_actions']
            or head['delivery_preparation']['unresolved_ref'] is not None
            or budget.execution_blocked(root)):
        raise ResumeUnavailable('technical_restart_original_execution_unresolved')
    if any((execution.origin(root,later)/'native-intent.json').exists()
            for later in fork_runtime.local_steps(root,include_native=True) if later > job['step']):
        raise ResumeUnavailable('worker_resume_successor_present')
    capability=backends.selected(settings).recovery_capability('technical_restart')
    if capability.get('status') != 'supported':
        raise ResumeUnavailable('technical_restart_unsupported_backend')
    original_facts=[]
    for index,(path,intent,outcome) in enumerate(zip(paths,intents,outcomes)):
        if index and intent.get('continuation_kind') != 'technical_restart':
            raise ResumeUnavailable('technical_restart_original_ineligible')
        facts=_prelaunch_original_facts(root,job['step'],path,attempt=index)
        _require_never_started(outcome,facts)
        original_facts.append(facts)
    node_limits=budget.node_limits(root,job,settings=settings)
    if node_limits is None:
        raise ResumeUnavailable('technical_restart_budget_unavailable')
    budget._attempt_consumption_from_facts(root,job,
        [(path,intent,outcome,None,False) for path,intent,outcome in zip(paths,intents,outcomes)],
        policy=settings,limits=node_limits,task_ref=c.record_ref(root/'jobs'/f'{job["step"]:03d}.json'))
    reason=budget.node_admission(root,job)
    if reason is not None:raise ResumeUnavailable(reason)
    limits=budget.snapshot(root)
    meter=native.usage(root)
    if (budget_limits.reached(limits['seconds_remaining'])
            or limits['max_tokens'] is not None and meter['accounted_tokens'] >= limits['max_tokens']
            or budget.used_steps(root) >= limits['max_steps']):
        raise ResumeUnavailable('worker_resume_budget_unavailable')
    return original_facts


def technical_stop(root, job, record, outcome):
    """Status owner qualifies an actionable pause using current admission."""
    from . import accepted_budget, native_recovery
    if not native_recovery.never_started(outcome):return None
    root=Path(root)
    head=accepted_budget.read(root)
    policy=c.settings_for_job(root,job,base=head.base,head=head)
    accepted=list(execution.accepted_intents_from_facts(root,job['step'],job=job,
        base=head.base,policy=policy,head=head,task_ref=c.record_ref(root/'jobs'/f'{job["step"]:03d}.json')))
    paths=[path for path,_ in accepted];intents=[intent for _,intent in accepted]
    attempt=len(paths)-1
    if not paths or paths[-1] != record:return None
    outcomes=[c.read(c.regular(path/'native-outcome.json')) for path in paths[:-1]]+[outcome]
    try:
        _technical_restart_gate(root,job,paths,intents,outcomes,head=head,settings=policy)
        available=True;reason=None
    except (ResumeUnavailable,budget.NodeBudgetUnavailable) as error:
        available=False;reason=getattr(error,'reason',str(error))
    except OSError:
        available=False;reason='technical_restart_original_unverified'
    return technical_stop_from_facts(job,outcome,recovery_available=available,
        blocked_reason=reason,attempt=attempt)


def _start_technical_restart(root, step, paths, *, outcome, expected_attempt):
    """Explicit replacement of a proven non-submission, never thread resume."""
    from . import accepted_budget, profiles
    root=Path(root)
    expected=0 if expected_attempt is None else expected_attempt
    if type(expected) is not int or expected < 0:
        raise ValueError('Technical restart requires an exact source attempt')
    intents=[c.read(c.regular(path/'native-intent.json')) for path in paths]
    if expected < len(paths)-1:
        replacement=intents[expected+1]
        if (replacement.get('continuation_kind') != 'technical_restart'
                or (replacement.get('technical_restart') or {}).get('source_attempt') != expected):
            raise ResumeUnavailable('technical_restart_source_attempt_changed')
        # A repeated operation collects the replacement it already accepted.
        # It cannot silently turn a failed replacement into another submission.
        return native.wait_native(root,step)
    if expected != len(paths)-1:
        raise ResumeUnavailable('technical_restart_source_attempt_changed')
    job=validate_task(root,step)
    head=accepted_budget.read(root)
    settings=c.settings_for_job(root,job,base=head.base,head=head)
    outcomes=[c.read(c.regular(path/'native-outcome.json')) for path in paths[:-1]]+[outcome]
    original_facts=_technical_restart_gate(root,job,paths,intents,outcomes,head=head,settings=settings)
    original=intents[0]
    profiles.validate_intent(settings,job,original)
    binding=profiles.binding(settings,job)
    # The failed attempt remains missing its billing receipt. Closing accepted
    # accounting retains that unknown coverage and all previously charged cost.
    current=head['current_native']
    if current is not None:
        if current['record'] != paths[-1].relative_to(root).as_posix():
            raise ResumeUnavailable('technical_restart_current_attempt_changed')
        accepted_budget.accept_terminal_from_base(root,step,paths[-1],base=head.base,
            meter=None,unknown=True,metering=outcome.get('metering'))
    budget.transition(root,'advance','explicit_technical_restart')
    anchor=budget.accept_continuation(root,job,paths)
    number=len(paths)
    record=execution.origin(root,step)/'continuations'/f'{number:03d}'
    intent=dict(step=step,attempt=number,continuation_kind='technical_restart',
        context_mode='new_context',native_home=str(root/'native-homes'/f'{step:03d}'/f'context-{number:03d}'),
        **binding,task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'),
        root_intent_ref=c.record_ref(paths[0]/'native-intent.json'),previous=execution.proof(paths[-1]),
        budget_slice=anchor,technical_restart=dict(source_attempt=number-1,
            outcome_ref=c.record_ref(paths[-1]/'native-outcome.json')))
    validate_technical_restart(root,job,intent,prior_paths=paths,prior_intents=intents,
        outcomes=outcomes,original_facts=original_facts)
    record.mkdir(parents=True,exist_ok=True)
    accepted_budget.accept_native(root,step,record,intent)
    # run_native repeats the backend's real capability/permission preflight in
    # its unchanged launch path before it can submit any model request.
    runtime.start(root,step,number)
    return native.wait_native(root,step,_reentry_started=True)


def automatic_source(root, step, record):
    """Interpret retained native failures while verifying original native facts.

    The saved failure projection is a diagnostic, not recovery authority. Read
    its current interpretation from the exact native stream without rewriting
    that stream or receipt; terminal, activity and session facts must still
    agree with the original process outcome.
    """
    from . import backends
    policy = configuration_updates.for_step(root, step)
    backend = backends.selected(policy)
    if backend.recovery_capability('automatic_recovery').get('status') != 'supported':
        raise ResumeUnavailable('automatic_recovery_unsupported_backend')
    outcome = c.native_outcome(record)
    intent = c.read(c.regular(record / 'native-intent.json'))
    return _automatic_source_from_facts(record, policy=policy, intent=intent, outcome=outcome)


def _native_writer_facts(record, outcome):
    """Use the exact stopped writer's compact facts and bound original stat."""
    from .native_usage import stream_binding
    expected = outcome.get('native_stream')
    if not isinstance(expected, dict):
        raise ResumeUnavailable('native_compact_control_facts_unavailable')
    if stream_binding(Path(record) / 'native.jsonl') != expected:
        raise ValueError('Original native stream changed')
    if (type(outcome.get('terminal_observed')) is not bool
            or type(outcome.get('native_activity_observed')) is not bool):
        raise ValueError('Native writer control facts are unavailable')
    return outcome


def _automatic_source_from_facts(record, *, policy, intent, outcome):
    """Accepted writer facts already own native failure/activity/session meaning."""
    from . import backends
    backend = backends.selected(policy)
    if backend.recovery_capability('automatic_recovery').get('status') != 'supported':
        raise ResumeUnavailable('automatic_recovery_unsupported_backend')
    return dict(_native_writer_facts(record, outcome))


def format_source(root, step, record):
    """Verify pre-science format rejection from originals, not saved diagnostics.

    Older receipts may retain an unknown error projection. That projection is
    never authority for negotiation; the complete raw native stream supplies
    the positive request rejection, with every other retained fact checked.
    """
    from . import backends
    policy = configuration_updates.for_step(root, step)
    backend = backends.selected(policy)
    if backend.recovery_capability('format_fallback').get('status') != 'supported':return None
    outcome = c.read(c.regular(record / 'native-outcome.json'))
    if not _format_source_outcome(outcome):return None
    intent = c.read(c.regular(record / 'native-intent.json'))
    return _format_source_from_facts(record, policy=policy, intent=intent, outcome=outcome)


def _format_source_outcome(outcome):
    return not (outcome.get('terminal_observed') is not False or type(outcome.get('returncode')) is not int
            or outcome['returncode'] == 0 or outcome.get('stop_reason') not in {None,'native_failure_event'}
            or outcome.get('cleanup') != 'observed_group_cleaned'
            or outcome.get('native_schema_transport', 'native_flag') != 'native_flag')


def _format_source_from_facts(record, *, policy, intent, outcome):
    from . import backends
    backend = backends.selected(policy)
    if backend.recovery_capability('format_fallback').get('status') != 'supported' or not _format_source_outcome(outcome):
        return None
    facts = _native_writer_facts(record, outcome)
    return dict(facts, format_rejected=True) if facts.get('native_format_rejected') is True else None


def _automatic_session(root, step, record, outcome):
    identity = outcome.get('native_identity') or {}
    original = c.read(c.regular(record / 'native-intent.json'))
    retained = original.get('session') or {}
    if retained and outcome.get('thread_id') == retained.get('thread_id'):
        from . import backends
        backend=backends.selected(configuration_updates.for_step(root,step))
        backend.validate_resume_session(retained,c.workspace(root,step),retained['home'])
        return retained
    return session(root, step, record, home=identity.get('home'), session_path=identity.get('path'))


def _completed_work_request(root, step, record, source_attempt, prior_intents, *, inventory):
    """Only the existing exact W delivery contract can reopen completed work."""
    if inventory is None:return None
    job = inventory.job
    if job['step'] != step:
        raise ValueError('Current inventory belongs to another task')
    if (job['role'] != 'W' or job.get('formal_review') or job.get('final_only')
            or job.get('purpose') == 'closeout'
            or any(item.get('continuation_kind') == 'author_closeout' for item in prior_intents)
            or not (record / 'work-evidence.json').exists() or (record / 'stage-end.json').exists()):
        return None
    from . import worker_delivery
    if not worker_delivery.repair_applicable(job):return None
    outcome = c.native_outcome(record)
    if (outcome.get('returncode') != 0 or outcome.get('stop_reason')
            or outcome.get('output_status') == 'missing'
            or outcome.get('usage', {}).get('unknown_steps') and not budget.native_completion_known(
                outcome, configuration_updates.for_step(root, step))):
        return None
    try:
        science = c.science_payload(record / 'science-output.json', 'W', job.get('handoff_protocol', 1),
            **c.response_options(job))
        # A valid continuation fragment retains its original fragment route.
        if science.get('stage_intent') in {'continue','handoff','revise_h','review'}:return None
        delivery=worker_delivery.assessment(root,job,science,record,inventory=inventory,source_attempt=source_attempt)
        if delivery['status']!='incomplete':
            return None
        worker_delivery.require_output_clarification(science,delivery)
        request = output_request(root, step, record,inventory=inventory, source_attempt=source_attempt, verify_task=False)
    except OutputRepairRequired:
        return None
    return request if request['code'] == 'worker_delivery_incomplete' else None


def automatic_decision(root, step, *, inventory=None):
    """One read-only arbiter; existing successful repair/fragment paths win."""
    from . import backends, native_recovery
    root = Path(root)
    paths = execution.records(root, step)
    last = paths[-1]
    used = native_recovery.counters(c.read(c.regular(path / 'native-intent.json')) for path in paths)
    base = dict(action='none', reason='automatic_recovery_disabled', source_attempt=len(paths)-1,
        source_record=str(last), delay_seconds=0., retry_after_seconds=None, counters=used)
    settings = configuration_updates.for_step(root, step)
    supplied = (settings.get('resume_policy') or {}).get('automatic_recovery')
    if supplied is None:return base
    limits = native_recovery.policy(supplied)
    if not limits['enabled']:return base
    def stop(reason):return dict(base, action='stop', reason=reason)
    if (execution.origin(root, step) / 'result-identity.json').exists():
        return dict(base, reason='automatic_recovery_stage_sealed')
    if runtime.node_state(root, step) != 'terminal':
        return dict(base, reason='automatic_recovery_original_not_terminal')
    job = c.read(c.regular(root / 'jobs' / f'{step:03d}.json'))
    outcome = c.native_outcome(last)
    completed = outcome.get('terminal_observed') is True
    work_request = None
    if completed:
        work_request = _completed_work_request(root, step, last, len(paths)-1,
            [c.read(c.regular(path / 'native-intent.json')) for path in paths],inventory=inventory)
        if work_request is None:return dict(base, reason='automatic_recovery_completed_turn')
        if checkpoint.enabled(root,job):
            review=fragment_review(root,job)
            if review['requested'] and review['enforced']:
                return dict(base, reason='worker_fragment_review_requested')
    original = c.read(c.regular(last / 'native-intent.json'))
    format_outcome = (format_source(root, step, last)
        if not completed and (last / 'native.jsonl').exists() else None)
    if (original.get('continuation_kind') == 'author_closeout'
            or not completed and (original.get('output_repair')
                or original.get('work_repair') and original.get('continuation_kind')!='automatic_recovery')):
        return stop('automatic_recovery_special_scope_unavailable')
    final=budget.final_a_barrier(root)
    closeout=budget.closeout_state(root)['begin']
    boundary=(job.get('final_only') or job.get('purpose')=='closeout' or final is not None or closeout is not None)
    same_scope=(final is not None and final['step']==step
        and (original.get('budget_slice') or {}).get('final_return') is not None
        or final is None and job.get('purpose')=='closeout')
    if boundary and (format_outcome is None or not same_scope):
        return stop('automatic_recovery_final_boundary')
    if (root / 'pause.request').exists():return stop('worker_resume_paused_or_closeout')
    from . import correction
    if correction.gate(root, step - 1) is not None:return stop('worker_resume_correction_paused')
    if outcome.get('stop_reason') not in {None, 'native_failure_event'}:
        return stop(outcome['stop_reason'])
    backend = backends.selected(settings)
    if backend.recovery_capability('automatic_recovery').get('status') != 'supported':return stop('automatic_recovery_unsupported_backend')
    if outcome.get('cleanup') != 'observed_group_cleaned':return stop('automatic_recovery_cleanup_unknown')
    binding = execution.identity(root, step, len(paths)-1)
    for name in ('runner-process.json', 'native-process.json'):
        receipt = c.read(c.regular(last / name))
        if (receipt.get('task_ref') != binding
                or not isinstance(receipt.get('boot_id'), str) or not receipt['boot_id']
                or any(type(receipt.get(key)) is not int or receipt[key] <= 0
                    for key in ('pid','start_ticks','pgid','sid','ppid'))
                or c.record_ref(last / name) != outcome.get('execution_processes', {}).get(name)):
            raise ValueError('Automatic recovery original process identity changed')
        if runtime.process_matches(receipt, binding):return dict(base, reason='worker_resume_original_running')
    from . import history, execution_recovery
    if execution_recovery.pending(root):return stop('automatic_recovery_managed_execution_pending')
    if job['role'] == 'W' and inventory is not None and any(row['status'] not in {'known_success','known_failed','not_submitted'}
            for row in history.managed_calls(inventory)):
        return stop('automatic_recovery_managed_execution_pending')
    outcome = format_outcome or automatic_source(root, step, last)
    try:_automatic_session(root, step, last, outcome);available = True
    except ResumeUnavailable:available = False
    if format_outcome is not None:
        decided = native_recovery.format_decision(limits, used, session_available=available,
            already_fallback=any((c.read(c.regular(path / 'native-intent.json')).get('automatic_recovery') or {}).get('format_fallback')
                for path in paths))
        return dict(base, **decided)
    if work_request is not None:
        decided = native_recovery.work_decision(limits, used, session_available=available)
        return dict(base, **decided, work_repair=work_request)
    decided = native_recovery.decision(outcome, limits, used, session_available=available,
        activity_observed=outcome.get('native_activity_observed', False),
        jitter=((len(paths) * 37 % 101) / 50. - 1))
    return dict(base, **decided)


def validate_automatic(root, step, intent, *, prior_record=None, prior_intents=None, inventory=None):
    from . import native_recovery
    request = intent.get('automatic_recovery')
    settings = configuration_updates.for_step(root, step)
    supplied = (settings.get('resume_policy') or {}).get('automatic_recovery')
    if supplied is None:raise ValueError('Automatic recovery has no independent authorization')
    limits = native_recovery.policy(supplied)
    if prior_record is None:
        chain = execution.records(root, step)
        prior_record = chain[intent['attempt']-1]
        prior_intents = [c.read(c.regular(path / 'native-intent.json')) for path in chain[:intent['attempt']]]
    if prior_intents is None:raise ValueError('Automatic recovery requires its exact prior attempt chain')
    format_fallback = isinstance(request,dict) and request.get('format_fallback') is True
    if isinstance(request,dict) and 'format_fallback' in request and not format_fallback:
        raise ValueError('Automatic format fallback marker changed')
    outcome = (format_source(root, step, prior_record) if format_fallback else
        automatic_source(root, step, prior_record))
    if outcome is None:raise ValueError('Automatic format fallback has no positive original rejection')
    job = c.read(c.regular(Path(root) / 'jobs' / f'{step:03d}.json'))
    prior_intent = prior_intents[-1]
    work_request = (_accepted_repair_request(root,step,intent,prior_record) if intent.get('work_repair') is not None else None)
    if work_request is None and outcome.get('terminal_observed') is True:
        work_request=_completed_work_request(root,step,prior_record,intent['attempt']-1,prior_intents,inventory=inventory)
    if work_request is not None:
        if (outcome.get('terminal_observed') is not True or (prior_record/'stage-end.json').exists()
                or any(item.get('continuation_kind') == 'author_closeout' for item in prior_intents)):
            raise ValueError('Automatic work recovery cannot reopen a closed or special task')
        expected = native_recovery.work_decision(limits, native_recovery.counters(prior_intents),
            session_available=bool(intent.get('session')))
        if intent.get('work_repair') != work_request or intent.get('output_repair') is not None:
            raise ValueError('Automatic work recovery differs from its exact original delivery failure')
    elif not format_fallback and (job.get('final_only') or job.get('purpose') == 'closeout'
            or prior_intent.get('continuation_kind') == 'author_closeout'
            or prior_intent.get('output_repair')
            or prior_intent.get('work_repair') and prior_intent.get('continuation_kind')!='automatic_recovery'
            or intent.get('work_repair') or intent.get('output_repair')
            or not native_recovery.recoverable_source(outcome)):
        raise ValueError('Automatic recovery source is not an eligible unfinished task')
    used = native_recovery.counters(prior_intents)
    if format_fallback:
        if (prior_intent.get('continuation_kind') == 'author_closeout'
                or prior_intent.get('output_repair') or prior_intent.get('work_repair')
                or intent.get('work_repair') or intent.get('output_repair')):
            raise ValueError('Automatic format fallback changed its original scope')
        try:_automatic_session(root,step,prior_record,outcome);available=True
        except ResumeUnavailable:available=False
        expected = native_recovery.format_decision(limits, used, session_available=available,
            already_fallback=any((item.get('automatic_recovery') or {}).get('format_fallback') for item in prior_intents))
    elif work_request is None:
        expected = native_recovery.decision(outcome, limits, used,
            session_available=bool(intent.get('session')), activity_observed=outcome.get('native_activity_observed', False),
            jitter=((intent['attempt'] * 37 % 101) / 50. - 1))
    if (not isinstance(request, dict) or expected['action'] not in {'continue','retry'}
            or any(request.get(key) != expected.get(key) for key in ('action','reason','startup_retry'))
            or request.get('source_attempt') != intent['attempt']-1
            or request.get('counters') != used
            or type(request.get('wait_seconds')) not in (int, float)
            or not 0 <= request['wait_seconds'] <= limits['max_wait_seconds'] - used['wait_seconds']):
        raise ValueError('Automatic recovery differs from its original bounded failure')
    if request['startup_retry']:
        if intent.get('session') is not None:raise ValueError('Unaccepted startup retry cannot acquire a session')
    elif intent.get('session') != _automatic_session(root, step, prior_record, outcome):
        raise ValueError('Automatic recovery changed its original session')


def automatic_start(root, step, decision, *, inventory=None, recovery_wait=None):
    with budget._operator_writer(root):
        return _automatic_start_locked(root, step, decision, inventory=inventory,recovery_wait=recovery_wait)


def _automatic_start_locked(root, step, decision, *, inventory=None, recovery_wait=None):
    """Accept one replacement under the existing owner/controller writer."""
    from . import profiles, native_recovery
    root = Path(root)
    current = automatic_decision(root, step,inventory=inventory)
    if current != decision or current['action'] not in {'continue','retry'}:
        raise ResumeUnavailable('automatic_recovery_decision_stale')
    validate_launcher(root, step)
    job = validate_task(root, step,inventory=inventory)
    if inventory is None:
        from . import handoff_state
        job = handoff_state.material_inputs(root,job,sources={})
    paths = execution.records(root, step);last = paths[-1]
    from . import fork_runtime
    if any((execution.origin(root, later) / 'native-intent.json').exists()
            for later in fork_runtime.local_steps(root, include_native=True) if later > step):
        raise ResumeUnavailable('worker_resume_successor_present')
    waited = (recovery_wait or {}).get('waited_seconds', 0.)
    limits = native_recovery.policy(configuration_updates.for_step(root, step)['resume_policy']['automatic_recovery'])
    if (type(waited) not in (int, float) or not 0 <= waited <= limits['max_wait_seconds']-current['counters']['wait_seconds']):
        raise ResumeUnavailable('automatic_recovery_wait_limit')
    if current['delay_seconds'] and recovery_wait is None:
        raise ResumeUnavailable('automatic_recovery_wait_required')
    request = {key:current[key] for key in ('action','reason','source_attempt','counters','startup_retry')}
    if current.get('format_fallback') is True:request['format_fallback'] = True
    request['wait_seconds'] = waited
    if recovery_wait is not None:
        state_path = last / 'automatic-recovery.json'
        if (recovery_wait.get('record_ref') != c.record_ref(c.regular(state_path))
                or c.read(state_path).get('local_wait_seconds') != waited
                or c.read(state_path).get('status') != 'ready'):
            raise ValueError('Automatic recovery wait differs from its durable original')
        request['wait_record'] = recovery_wait['record_ref']
    budget.transition(root, 'advance', 'accepted_automatic_recovery')
    anchor = budget.accept_continuation(root, job, paths, automatic_recovery=current,inventory=inventory)
    settings = configuration_updates.for_step(root, step)
    binding = profiles.binding(settings, job)
    profiles.validate_intent(settings, job, c.read(c.regular(paths[0] / 'native-intent.json')))
    number = len(paths)
    record = execution.origin(root, step) / 'continuations' / f'{number:03d}'
    record.mkdir(parents=True, exist_ok=True)
    intent = dict(step=step, attempt=number, continuation_kind='automatic_recovery', **binding,
        task_ref=c.record_ref(root / 'jobs' / f'{step:03d}.json'),
        root_intent_ref=c.record_ref(paths[0] / 'native-intent.json'), previous=execution.proof(last),
        budget_slice=anchor, automatic_recovery=request)
    if current.get('work_repair') is not None:intent['work_repair'] = current['work_repair']
    if not request['startup_retry']:
        intent['session'] = _automatic_session(root, step, last, c.read(last / 'native-outcome.json'))
    validate_automatic(root, step, intent, prior_record=last,
        prior_intents=[c.read(c.regular(path / 'native-intent.json')) for path in paths],inventory=inventory)
    from . import accepted_budget
    accepted_budget.accept_native(root,step,record,intent)
    runtime.start(root, step, number)
    return intent


def policy(value, launcher, *, backend=None):
    required={'max_attempts','protocol','usage_scope'}
    if (not isinstance(value, dict) or not required <= set(value)
            or set(value)-required-{'output_repair','output_only','execution_wake','native_home','automatic_recovery'}
            or type(value['max_attempts']) is not int or not 1 <= value['max_attempts'] <= 2
            or ('output_repair' in value and type(value['output_repair']) is not bool)
            or ('execution_wake' in value and type(value['execution_wake']) is not bool)
            or ('output_only' in value and (type(value['output_only']) is not bool
                or value['output_only'] and not value.get('output_repair',False)))
            or ('native_home' in value and (not value.get('output_only') or not isinstance(value['native_home'],str)
                or not Path(value['native_home']).is_absolute() or Path(value['native_home']).resolve()!=Path(value['native_home'])))):
        raise ValueError('Resume requires explicit finite attempts and qualified turn-scoped launcher protocol')
    from . import backends
    backends.selected(dict(native_backend=backends.DEFAULT_BACKEND if backend is None else backend,
        native_launcher_argv=launcher)).validate_resume_policy(value)
    if not launcher or not Path(launcher[0]).is_absolute() or not Path(launcher[0]).is_file():
        raise ValueError('Resume requires an exact frozen executable, never PATH selection')
    files = []
    for argument in launcher:
        path = Path(argument).absolute()
        if path.is_file():
            original = c.regular(path.resolve())
            files.append(dict(invoked_path=str(path), path=str(original)))
    if not files:raise ValueError('Resume-capable launcher originals must be frozen')
    result = dict(value, launcher_files=files)
    if 'automatic_recovery' in value:
        from . import native_recovery
        result['automatic_recovery'] = native_recovery.policy(value['automatic_recovery'])
    return result


def validate_launcher(root, step=None):
    from . import backends
    settings = configuration_updates.for_step(root, step) if step is not None else configuration_updates.current(root)
    if backends.selected(settings).recovery_capability('resume').get('status') != 'supported':
        raise ResumeUnavailable('worker_resume_unsupported_backend')
    limits = settings.get('resume_policy')
    if limits is None:raise ResumeUnavailable('worker_resume_unsupported')
    for proof in limits['launcher_files']:
        if Path(proof['invoked_path']).resolve() != Path(proof['path']):
            raise ValueError('Frozen resume launcher changed')
        c.regular(Path(proof['path']))
    return limits


def wake_allowed(limits):
    """Execution completion has its own opt-in, separate from JSON repair."""
    return limits is not None and limits.get('execution_wake', not limits.get('output_only', False)) is True


def validate_wake_policy(root, step):
    limits = validate_launcher(root, step)
    if not wake_allowed(limits):
        raise ResumeUnavailable('worker_execution_wake_unauthorized')
    return limits



def validate_task(root, step, *, inventory=None):
    retained = c.read(c.regular(root / 'jobs' / f'{step:03d}.json'))
    job = retained
    if inventory is not None:
        if c.job_record(inventory.job)!=retained or inventory.job['step']!=step:
            raise ValueError('Current inventory belongs to another task')
        job = inventory.job
    # This is the original call's frozen transport input. Rebuilding it with a
    # later scientific contract would change a retained task during recovery.
    c.regular(execution.origin(root, step) / 'science-result.schema.json', limit=None)
    return job

def validate_session_header(line, thread, workspace):
    event = json.loads(line)
    if (not isinstance(event,dict) or not isinstance(event.get('payload'),dict)
            or event.get('type') != 'session_meta' or event.get('payload', {}).get('id') != thread
            or event['payload'].get('cwd') != str(workspace)):
        raise ValueError('Native session identity or workspace changed')


def session(root, step, record, *, home=None, session_path=None, identity_proof=False):
    """Retain exact hook/session metadata; the transcript is an opaque original."""
    from . import backends
    from .native_usage import MetadataUnavailable
    backend=backends.selected(configuration_updates.for_step(root, step))
    if backend.recovery_capability('resume').get('status') != 'supported':
        raise ResumeUnavailable('worker_resume_unsupported_backend')
    outcome = c.native_outcome(record)
    thread = outcome.get('thread_id')
    identity = outcome.get('native_identity') or {}
    value = identity.get('session')
    if not isinstance(value, dict):
        raise ResumeUnavailable('worker_resume_metadata_unavailable')
    if thread is None or value.get('thread_id') != thread:
        raise ResumeUnavailable('worker_resume_thread_unavailable')
    actual_home = home if home is not None else identity.get('home')
    if actual_home is None:
        raise ResumeUnavailable('worker_resume_metadata_unavailable')
    if session_path is not None and str(session_path) != value.get('path'):
        raise ValueError('Native session original path changed')
    try:
        backend.validate_resume_session(value,c.workspace(root, step),actual_home)
        backend.validate_session_event(outcome.get('native_thread_event'),thread)
    except MetadataUnavailable as error:
        raise ResumeUnavailable(str(error)) from error
    thread_event = outcome.get('native_thread_event')
    return dict(value, **(dict(thread_event=thread_event) if identity_proof else {}))


def interruption(root, job, record):
    """Retain observed consumption at reentry, without inventing an outcome."""
    saved = c.read(c.regular(record / 'interruption.json'))
    binding = (c.record_ref(root / 'jobs' / f'{job["step"]:03d}.json')
               if record == execution.origin(root, job['step']) else c.record_ref(record / 'native-intent.json'))
    if (saved.get('task_ref') != c.record_ref(root / 'jobs' / f'{job["step"]:03d}.json')
            or saved.get('intent_ref') != c.record_ref(record / 'native-intent.json')
            or type(saved.get('advance_seconds')) not in (int, float)):
        raise ValueError('Interrupted original identity changed')
    for name in ('runner-process.json', 'native-process.json'):
        identity = c.read(c.regular(record / name))
        if (saved.get('processes', {}).get(name) != c.record_ref(record / name)
                or identity.get('task_ref') != binding
                or not isinstance(identity.get('boot_id'), str) or not identity['boot_id']
                or identity['boot_id'] == saved.get('stamp', {}).get('boot_id')
                or any(type(identity.get(key)) is not int or identity[key] <= 0
                       for key in ('pid','start_ticks','pgid','sid','ppid'))):
            raise ValueError('Interrupted original process binding changed')
    return saved


def validate_reentry(root, job, intent, prior):
    saved = interruption(root, job, prior)
    original = c.read(c.regular(prior / 'native-intent.json'))
    anchor = intent.get('budget_slice', {})
    expected = dict(original['budget_slice'], boot_id=anchor.get('boot_id'),
        advance_seconds=saved['advance_seconds'],
        previous_tokens=saved['budget_slice']['observed_tokens'],
        previous_seconds=saved['budget_slice']['advance_seconds'])
    policy = configuration_updates.for_step(root, job['step']).get('resume_policy') or {}
    default_home = policy.get('native_home') if policy.get('output_only') else None
    home = original.get('native_home', original.get('session', {}).get('home',
        default_home or str(root / 'native-homes' / f'{job["step"]:03d}')))
    selected = intent.get('session') or {}
    if (intent.get('interruption') != c.record_ref(prior / 'interruption.json')
            or not isinstance(anchor.get('boot_id'), str) or not anchor['boot_id']
            or anchor != expected
            or selected.get('home') != home
            or selected != session(root, job['step'], prior, home=home, session_path=selected.get('path'))):
        raise ValueError('Reentry changed its original session or consumed budget')


def reenter(root, step):
    """Resume the original local session after reboot, retaining all old facts."""
    with budget._operator_writer(root):
        return _reenter_locked(root, step)


def _reenter_locked(root, step):
    inventory=None
    from .protocol import require_current
    require_current(root)
    # Reentry continues an already accepted full task. The policy's output-only
    # restriction still governs new repairs, not this interrupted task.
    validate_launcher(root, step)
    job = validate_task(root, step,inventory=inventory)
    require_current(root, job)
    paths = execution.records(root, step); prior = paths[-1]
    state = runtime.node_state(root, step)
    if state in {'running','terminal','sealed','waiting','unstarted'}:
        return c.read(c.regular(prior / 'native-intent.json'))
    if state != 'interrupted':
        raise ResumeUnavailable('worker_resume_original_not_interrupted')
    session_ref = session(root, step, prior)
    settings = configuration_updates.for_step(root, step)
    from . import profiles
    original = c.read(c.regular(prior / 'native-intent.json'))
    if (original.get('continuation_kind') == 'author_closeout'
            or original.get('output_repair') or original.get('work_repair')
            or not isinstance(original.get('budget_slice'), dict)):
        raise ResumeUnavailable('worker_resume_interrupted_scope_unavailable')
    profile_binding = profiles.binding(settings, job)
    profiles.validate_intent(settings, job, original)
    budget.transition(root, 'advance', 'resume_interrupted_original')
    marker = prior / 'interruption.json'
    if not marker.exists():
        stamp = budget.clock_stamp(); snapshot = budget.snapshot(root, sample_stamp=stamp)
        meter = c.read(c.regular(prior / 'usage.json')) if (prior / 'usage.json').exists() else None
        reported = meter or dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)
        if native._usage_event(dict(type='turn.completed', usage=reported)) != reported:
            raise ValueError('Interrupted native usage changed')
        report = dict(role=job['role'], limits=original['budget_slice']['limits'],
            advance_seconds=budget._node_duration(root,job,original,snapshot),
            observed_tokens=original['budget_slice'].get('previous_tokens',0)+reported['input_tokens']+reported['output_tokens'],
            dispatches=len(paths), usage_observed=meter is not None, **reported)
        c.write(marker, dict(task_ref=c.record_ref(root / 'jobs' / f'{step:03d}.json'),
            intent_ref=c.record_ref(prior / 'native-intent.json'), stamp=stamp,
            advance_seconds=snapshot['advance_seconds'], budget_slice=report,
            processes={name:c.record_ref(prior/name) for name in ('runner-process.json','native-process.json')}))
    saved = interruption(root, job, prior)
    # Validate the saved prefix against the same persisted attempt meters used
    # for ordinary continuation admission before publishing a new reentry.
    tokens, seconds = budget._attempt_consumption(root, job, paths)
    anchor = dict(original['budget_slice'], boot_id=budget.clock_stamp()['boot_id'],
        advance_seconds=saved['advance_seconds'], previous_tokens=tokens, previous_seconds=seconds)
    number = len(paths); record = execution.origin(root, step) / 'continuations' / f'{number:03d}'
    record.mkdir(parents=True, exist_ok=True)
    intent = dict(step=step, attempt=number, continuation_kind='reentry', **profile_binding,
        task_ref=c.record_ref(root / 'jobs' / f'{step:03d}.json'),
        root_intent_ref=c.record_ref(paths[0] / 'native-intent.json'), previous=execution.proof(prior),
        session=session_ref, budget_slice=anchor, interruption=c.record_ref(marker))
    validate_reentry(root, job, intent, prior)
    from . import accepted_budget
    # A reboot ends this local writer without producing a native outcome.
    # Close its accepted accounting with the retained observed lower bound;
    # the existing operator writer keeps this settlement and the replacement
    # acceptance in one recovery operation, and both ledger writes can resume.
    report = saved['budget_slice']
    meter = ({key: report[key] for key in ('input_tokens', 'cached_input_tokens', 'output_tokens')}
        if report['usage_observed'] else None)
    accepted_budget.accept_terminal(root,step,prior,meter=meter,unknown=True)
    accepted_budget.accept_native(root,step,record,intent)
    runtime.start(root, step, number)
    return intent



def _work_exit(root,job,record,reason,*,inventory):
    from . import worker_delivery
    paths=execution.records(root,job['step'])
    _eligible_original(root,job['step'],paths,successful=True,wait_runner=True,
        budgeted=budget.node_limits(root,job) is not None)
    proof=worker_delivery.authorize_exit(root,job,record,reason)
    checkpoint.end_stage(root,job,record,reason,inventory=inventory,work_exit=proof)
    return c.seal_result_from_inventory(root,job['step'],inventory=inventory)


def finish_automatic_work(root, step, decision, reason, *, inventory):
    """Retain the exact completed contribution at a verified host boundary."""
    with budget._operator_writer(root):
        current=automatic_decision(root,step,inventory=inventory)
        if (current!=decision or current.get('work_repair') is None
                or current['action'] not in {'continue','stop'}):
            raise ResumeUnavailable('automatic_recovery_decision_stale')
        if reason not in {'worker_resume_node_exhausted','worker_resume_budget_unavailable',
                'automatic_recovery_continue_limit','wall_limit','token_limit','step_limit'}:
            raise ValueError('Automatic work exit requires a verified original host budget or continue cap')
        if reason=='automatic_recovery_continue_limit' and current['reason']!=reason:
            raise ValueError('Automatic work exit has no exhausted original continue cap')
        job=validate_task(root,step,inventory=inventory)
        return _work_exit(Path(root),job,execution.record(root,step),reason,inventory=inventory)


def seal_or_repair(root, step, *, inventory):
    """Try the original seal first; only a frozen opt-in can accept a repair turn."""
    if inventory is None:return c.seal_result(root,step)
    from .protocol import require_current_job
    c._inventory_identity(root,step,inventory)
    if inventory.base is None or inventory.policy is None:
        raise ValueError('Current inventory requires its produced base and policy')
    require_current_job(root,base=inventory.base,job=inventory.job,
        retained=inventory.job,policy=inventory.policy)
    if (execution.origin(root,step)/'result-identity.json').exists():
        return c.seal_result(root,step)
    try:
        job=inventory.job
        record=inventory.record
        if not inventory.science_available:return c.seal_result_from_inventory(root,step,inventory=inventory)
        if not (execution.origin(root,step)/'result-identity.json').exists():
            from . import managed_wait
            managed_wait.before_seal(root,job,record,inventory=inventory)
        if checkpoint.enabled_for_policy(job,settings=inventory.policy) and not (execution.origin(root, step) / 'result-identity.json').exists():
            if (record / 'stage-end.json').exists():
                checkpoint.stage_end(root, job, record)
                return c.seal_result_from_inventory(root, step,inventory=inventory)
            saved = checkpoint.validate(root, job, record,inventory=inventory)
            outcome=inventory.native_outcome
            if (outcome.get('returncode') == 0 and not outcome.get('stop_reason')
                    and (not outcome['usage']['unknown_steps'] or budget.native_completion_known(outcome, inventory.policy))):
                if saved['science']['status'] != 'present':
                    # The seal consumer provides the bound output diagnosis/repair.
                    return c.seal_result_from_inventory(root, step,inventory=inventory)
                science=inventory.payload
                if science is None:
                    if inventory.science_error is not None:raise inventory.science_error
                    if inventory.output_error is not None:raise inventory.output_error
                    raise ValueError('Current inventory requires its produced scientific payload')
                review = fragment_review(root, job)
                review_required = review['requested'] and review['enforced']
                from . import worker_delivery
                delivery=worker_delivery.assessment(root,job,science,record,inventory=inventory)
                if science['stage_intent']!='continue':worker_delivery.require_output_clarification(science,delivery)
                if (science['stage_intent'] in {'handoff','revise_h','review'} or review_required and science['stage_intent']=='continue') and (
                        any(call['status']=='unknown' for call in inventory.managed_calls)
                        or ((job.get('search') or {}).get('breadth') or {}).get('prior_unknown')):
                    raise ResumeUnavailable('worker_handoff_original_execution_unknown')
                if science['stage_intent'] in {'handoff','revise_h','review'}:
                    # Explicit ordinary W handoffs retain incomplete work; the
                    # original stopped-writer and managed-wait gates above still apply.
                    checkpoint.end_stage(root,job,record,'worker_'+science['stage_intent'],inventory=inventory)
                    return c.seal_result_from_inventory(root,step,inventory=inventory)
                if review_required and science['stage_intent']=='continue':
                    if delivery['status']=='incomplete':return _work_exit(root,job,record,review['reason'],inventory=inventory)
                    checkpoint.end_stage(root,job,record,review['reason'],inventory=inventory)
                    return c.seal_result_from_inventory(root,step,inventory=inventory)
                if delivery['status']=='partial':
                    checkpoint.end_stage(root,job,record,delivery['reason'],inventory=inventory)
                    return c.seal_result_from_inventory(root,step,inventory=inventory)
                if delivery['status']=='incomplete' and review_required:
                    return _work_exit(root,job,record,review['reason'],inventory=inventory)
                if science['stage_intent']!='continue':worker_delivery.require_delivery(root,job,science,record,inventory=inventory)
                if checkpoint.disposition(science, review_requested=review_required) == 'continue':
                    try:
                        fragment_start(root, step,inventory=inventory)
                    except budget.NodeBudgetUnavailable as error:
                        # A valid scientific contribution is retained at the
                        # original budget boundary, without another native turn.
                        if delivery['status']=='incomplete':return _work_exit(root,job,record,error.reason,inventory=inventory)
                        checkpoint.end_stage(root, job, record, error.reason,inventory=inventory)
                        return c.seal_result_from_inventory(root, step,inventory=inventory)
                    except ResumeUnavailable as error:
                        reason = str(error)
                        if reason == 'worker_fragment_review_requested':
                            reason = error.review_reason
                        elif reason not in {'worker_resume_paused_or_closeout', 'worker_resume_correction_paused'}:
                            raise
                        if delivery['status']=='incomplete':return _work_exit(root,job,record,reason,inventory=inventory)
                        checkpoint.end_stage(root, job, record, reason,inventory=inventory)
                        return c.seal_result_from_inventory(root, step,inventory=inventory)
                    return dict(fragment_continuation=True)
                checkpoint.end_stage(root, job, record, review['reason'] if review_required else 'worker_requested',inventory=inventory)
        return c.seal_result_from_inventory(root, step,inventory=inventory)
    except WorkRepairRequired as error:
        inventory=error.inventory
        job=inventory.job;record=inventory.record
        decision=automatic_decision(root,step,inventory=inventory)
        if decision['action'] in {'continue','retry'}:raise AutomaticRecoveryRequired(decision,inventory=inventory)
        if decision['reason']=='automatic_recovery_continue_limit' and decision.get('work_repair') is not None:
            return _work_exit(root,job,record,decision['reason'],inventory=inventory)
        try:return start(root,step,inventory=inventory)
        except budget.NodeBudgetUnavailable as error:return _work_exit(root,job,record,error.reason,inventory=inventory)
        except ResumeUnavailable as error:
            return _work_exit(root,job,record,str(error),inventory=inventory)
    except OutputRepairRequired:
        if not (inventory.policy.get('resume_policy') or {}).get('output_repair', False):
            raise
        try:return start(root, step,inventory=inventory)
        except ResumeUnavailable as error:
            error.status='output_repair_unavailable'
            raise


def output_request(root, step, record, *, inventory, source_attempt=None, verify_task=True):
    """Recompute a repair binding from verified original final-response bytes."""
    if verify_task:
        job = validate_task(root, step,inventory=inventory)
        if inventory is None:
            from . import handoff_state
            job = handoff_state.material_inputs(root,job,sources={})
    else:
        job = inventory.job if inventory is not None else c.read_job(root / 'jobs' / f'{step:03d}.json')
    if source_attempt is None:source_attempt = len(execution.records(root, step)) - 1
    outcome = c.native_outcome(record)
    if (type(outcome.get('returncode')) is not int or outcome['returncode'] != 0
            or outcome.get('stop_reason')
            or outcome['usage']['unknown_steps'] and not budget.native_completion_known(outcome, configuration_updates.for_step(root, step))):
        raise ValueError('Output repair requires a known completed original turn')
    raw = record / 'science-output.json'
    if outcome.get('output_status') == 'missing':
        if raw.exists() or raw.is_symlink() or (record / 'result.pending.json').exists() or (record / 'result.pending.json').is_symlink():
            raise ValueError('Missing final output identity changed')
        error = OutputRepairRequired('missing_output', 'Return complete scientific JSON; preserve original facts/plans/limits. No work this repair turn; reuse prior results/checkpoints only if they exist.')
    else:
        if c.file_ref(c.regular(raw, limit=None)) != outcome.get('raw_ref'):
            raise ValueError('Original final output changed before repair')
        if inventory is not None and inventory.output_error is not None:
            error=inventory.output_error
            return dict(**error.request(),source_attempt=source_attempt,
                outcome_ref=c.record_ref(record/'native-outcome.json'),raw_ref=outcome.get('raw_ref'))
        try:
            science=c.science_payload(raw, job['role'], job.get('handoff_protocol', 1), 'selection' in job,**c.response_options(job))
            from . import candidate_refs
            science=candidate_refs.control_payload(job,science,root=root,inventory=inventory)
            from . import formal_review
            formal_review.validate_choice(root,job,science,inventory=inventory)
            from . import semantic
            semantic.author_choices(root,job,science,inventory=inventory)
            from . import decision
            decision.require_request(job,science)
            if job['role']=='W':
                from . import worker_delivery, candidate_nominations
                worker_delivery.require_delivery(root,job,science,record,inventory=inventory,source_attempt=source_attempt)
                candidate_nominations.require(root,job,science,inventory=inventory)
            if inventory is not None:inventory.materials
        except OutputRepairRequired as failure:
            error = failure
        else:
            raise ValueError('A valid final response does not authorize an output repair')
    return dict(**error.request(), source_attempt=source_attempt,
                outcome_ref=c.record_ref(record / 'native-outcome.json'), raw_ref=outcome.get('raw_ref'),
                **(dict(work_evidence_ref=c.record_ref(record/'work-evidence.json')) if isinstance(error,WorkRepairRequired) else {}))


def _accepted_repair_request(root,step,intent,prior):
    """Validate a saved repair's exact source, without rejudging old diagnostics."""
    attempt=intent.get('attempt')
    if type(attempt) is not int or attempt<1:raise ValueError('Repair lost its original attempt')
    accepted=execution.origin(root,step)/'continuations'/f'{attempt:03d}'/'native-intent.json'
    if not accepted.exists():return None  # Admission still uses today's gates.
    request=intent.get('work_repair') or intent.get('output_repair')
    outcome=c.native_outcome(prior)
    job=c.read(c.regular(Path(root)/'jobs'/f'{step:03d}.json'))
    if (c.read(c.regular(accepted))!=intent or not isinstance(request,dict)
            or type(request.get('source_attempt')) is not int or request['source_attempt']!=attempt-1
            or not isinstance(request.get('code'),str) or not request['code']
            or not isinstance(request.get('diagnostic'),str)
            or request.get('outcome_ref')!=c.record_ref(prior/'native-outcome.json')
            or request.get('raw_ref')!=outcome.get('raw_ref')
            or type(outcome.get('returncode')) is not int or outcome['returncode']!=0
            or outcome.get('stop_reason')
            or outcome['usage']['unknown_steps'] and not budget.native_completion_known(outcome,configuration_updates.for_step(root,step))):
        raise ValueError('Accepted repair changed its exact completed source')
    raw=prior/'science-output.json'
    if outcome.get('output_status')=='missing':
        if (outcome.get('raw_ref') is not None or raw.exists() or raw.is_symlink()
                or (prior/'result.pending.json').exists() or (prior/'result.pending.json').is_symlink()):
            raise ValueError('Accepted missing output source changed')
    elif c.file_ref(c.regular(raw,limit=None))!=outcome.get('raw_ref'):
        raise ValueError('Accepted repair raw original changed')
    if intent.get('work_repair') is not None:
        if (job['role']!='W' or job.get('formal_review') or job.get('final_only') or job.get('purpose')=='closeout'
                or outcome.get('output_status')=='missing' or request.get('code')!='worker_delivery_incomplete'
                or request.get('work_evidence_ref')!=c.record_ref(prior/'work-evidence.json')
                or c.read_scientific_output(raw).get('stage_intent')=='continue'):
            raise ValueError('Accepted work repair changed its original scope')
    elif request.get('work_evidence_ref') is not None:
        raise ValueError('Output repair cannot acquire scientific work scope')
    return request


def validate_repair(root, step, intent, *, prior_record=None, prior_intents=None, verify_task=True, inventory=None):
    request = intent.get('work_repair') or intent.get('output_repair')
    if request is None:
        return
    automatic_work = intent.get('continuation_kind') == 'automatic_recovery' and intent.get('work_repair') is not None
    if automatic_work:
        validate_automatic(root, step, intent, prior_record=prior_record, prior_intents=prior_intents,inventory=inventory)
        return
    if (configuration_updates.for_step(root, step).get('resume_policy') or {}).get('output_only') and intent.get('work_repair') is not None:
        raise ValueError('Output-only policy does not authorize scientific work repair')
    if intent.get('work_repair') is None and not validate_launcher(root, step).get('output_repair', False):
        raise ValueError('Output repair is not authorized by the frozen policy')
    prior = prior_record if prior_record is not None else execution.records(root, step)[intent['attempt'] - 1]
    expected=_accepted_repair_request(root,step,intent,prior)
    if expected is None:
        expected=output_request(root,step,prior,inventory=inventory,source_attempt=intent['attempt']-1,verify_task=verify_task)
    if request != expected:
        raise ValueError('Accepted output repair differs from its original response failure')


def validate_repair_chain(root, step, chain):
    """Terminal execution checks every repair source without replaying projections."""
    for number, record in enumerate(chain[1:], 1):
        intent = c.read(c.regular(record / 'native-intent.json'))
        if intent.get('output_repair') is not None or intent.get('work_repair') is not None:
            # execution.records already verified task/attempt/previous identities.
            # Avoid terminal -> scientific projection -> terminal recursion.
            validate_repair(root, step, intent, prior_record=chain[number - 1],
                prior_intents=[c.read(c.regular(path / 'native-intent.json')) for path in chain[:number]],
                verify_task=False)


def validate_fragment_chain(root, step, chain):
    job = c.read(c.regular(root / 'jobs' / f'{step:03d}.json'))
    for number, record in enumerate(chain[1:], 1):
        intent = c.read(c.regular(record / 'native-intent.json'))
        if intent.get('continuation_kind') != 'fragment':
            continue
        settings = configuration_updates.for_step(root, step)
        if not checkpoint.enabled_for_policy(job, settings=settings):
            raise ValueError('Fragment continuation requires the original ordinary W task')
        prior = chain[number - 1]
        if c.read(c.regular(prior / 'native-intent.json')).get('continuation_kind') == 'author_closeout':
            raise ValueError('Fragment continuation cannot expand an author closeout scope')
        saved = c.read(c.regular(prior/'checkpoint.json'))
        original = c.native_outcome(prior)
        science = c.science_payload(prior / 'science-output.json', 'W', job.get('handoff_protocol', 1))
        if (saved['science']['status'] != 'present' or science.get('stage_intent') != 'continue'
                or original.get('returncode') != 0 or original.get('stop_reason')
                or original['usage']['unknown_steps'] and not budget.native_completion_known(original, configuration_updates.for_step(root, step))):
            raise ValueError('Fragment continuation has no qualified original continuing turn')
        mode = intent.get('context_mode')
        if mode != checkpoint.policy(settings['worker_fragment_policy'])['context']:
            raise ValueError('Fragment context mode changed its frozen policy')
        if mode == 'new_context' and intent.get('native_home') != str(root / 'native-homes' / f'{step:03d}' / f'context-{number:03d}'):
            raise ValueError('Fresh native context changed its exact attempt namespace')


def fragment_review(root, job):
    """Observe host investment even when the Worker did not call an adapter."""
    from . import experiments
    admission = budget.review_configuration(root, job)
    from .protocol import stage_scope
    scope=stage_scope(root,job)
    native_attempt = dict(number=len(execution.records(root, job['step'])) - 1,
                          execution_key=execution.identity(root, job['step']))
    return experiments.review_status(dict(admission=admission, scope=scope, native_attempt=native_attempt))


def _eligible_original(root, step, paths, *, successful=False, wait_runner=False, budgeted=True):
    """No accepted replacement until the exact old native writer is terminal."""
    last = paths[-1]
    outcome = c.native_outcome(last)
    if (type(outcome.get('returncode')) is not int or outcome['returncode'] == 0 and not successful
            or outcome.get('stop_reason') or outcome.get('cleanup') != 'observed_group_cleaned'):
        raise ResumeUnavailable('worker_resume_terminal_ineligible')
    meter = c.read(c.regular(last / 'usage.json')) if (last/'usage.json').exists() else None
    if meter is not None and native._usage_event(dict(type='turn.completed', usage=meter)) != meter:
        raise ResumeUnavailable('worker_resume_usage_unknown')
    if meter is None and not budget.native_completion_known(outcome, configuration_updates.for_step(root, step)):
        raise ResumeUnavailable('worker_resume_terminal_ineligible')
    binding = execution.identity(root, step, len(paths) - 1)
    for name in ('runner-process.json', 'native-process.json'):
        if c.record_ref(c.regular(last / name)) != outcome.get('execution_processes', {}).get(name):
            raise ValueError('Original execution process receipt changed')
        identity = c.read(c.regular(last / name))
        if (identity.get('task_ref') != binding or not isinstance(identity.get('boot_id'), str) or not identity['boot_id']
                or any(type(identity.get(key)) is not int or identity[key] <= 0
                       for key in ('pid', 'start_ticks', 'pgid', 'sid', 'ppid'))):
            raise ValueError('Original execution process identity is invalid')
        if name == 'runner-process.json' and wait_runner:
            until = time.monotonic() + 5
            while runtime.process_matches(identity, binding) and time.monotonic() < until:
                time.sleep(.05)
        if runtime.process_matches(identity, binding):
            raise ResumeUnavailable('worker_resume_original_running')
    if budgeted and not isinstance(outcome.get('budget_slice'), dict):
        raise ResumeUnavailable('worker_resume_usage_unknown')


def fragment_start(root, step, *, inventory):
    """Accept the next successful W fragment, never a failure retry or new job."""
    from .protocol import require_current
    require_current(root)
    from . import contracts as _contracts
    require_current(root,_contracts.read(_contracts.regular(Path(root)/'jobs'/f'{step:03d}.json')))
    root = Path(root)
    # Full ordinary fragments are authorized independently from output-only
    # repairs. A completed repair may restore a continuing W response, but the
    # repair turn itself retains its existing no-new-execution boundary.
    validate_launcher(root, step)
    job = validate_task(root, step,inventory=inventory)
    if not checkpoint.enabled(root, job):
        raise ResumeUnavailable('worker_fragments_unavailable')
    if (execution.origin(root, step) / 'result-identity.json').exists():
        raise ResumeUnavailable('worker_fragment_stage_already_sealed')
    if (root / 'pause.request').exists() or budget.closeout_state(root)['begin'] is not None:
        raise ResumeUnavailable('worker_resume_paused_or_closeout')
    from . import correction
    if correction.gate(root, step - 1) is not None:
        raise ResumeUnavailable('worker_resume_correction_paused')
    settings = configuration_updates.for_step(root, step)
    from . import fork_runtime
    for later in (number for number in fork_runtime.local_steps(root, include_native=True) if number > step):
        if (execution.origin(root, later) / 'native-intent.json').exists():
            raise ResumeUnavailable('worker_resume_successor_present')
    paths = execution.records(root, step)
    last = paths[-1]
    if c.read(c.regular(last / 'native-intent.json')).get('continuation_kind') == 'author_closeout':
        raise ResumeUnavailable('worker_fragment_special_scope_unavailable')
    saved = checkpoint.validate(root, job, last,inventory=inventory)
    science = c.science_payload(last / 'science-output.json', 'W', job.get('handoff_protocol', 1))
    if saved['science']['status'] != 'present' or checkpoint.disposition(science) != 'continue':
        raise ResumeUnavailable('worker_fragment_not_continuing')
    _eligible_original(root, step, paths, successful=True, wait_runner=True)
    review = fragment_review(root, job)
    if review['requested'] and review['enforced']:
        error = ResumeUnavailable('worker_fragment_review_requested')
        error.review_reason = review['reason']
        raise error
    mode = checkpoint.policy(settings['worker_fragment_policy'])['context']
    maximum = settings['worker_fragment_policy'].get('max_context_windows')
    windows = (inventory.native_outcome.get('metering') or {}).get('context_windows') or {}
    if (mode == 'new_context' and maximum is not None
            and type(windows.get('windows_started')) is int and windows['windows_started'] >= maximum):
        error = ResumeUnavailable('worker_fragment_review_requested')
        error.review_reason = 'worker_context_review_due'
        raise error
    session_ref = session(root, step, last) if mode == 'same_session' else None
    from . import profiles
    profile_binding = profiles.binding(settings, job)
    profiles.validate_intent(settings, job, c.read(paths[0] / 'native-intent.json'))
    # All previous targets and shared authorization are cumulatively consumed.
    budget.transition(root, 'advance', 'accepted_worker_fragment')
    anchor = budget.accept_continuation(root, job, paths,inventory=inventory)
    number = len(paths)
    record = execution.origin(root, step) / 'continuations' / f'{number:03d}'
    record.mkdir(parents=True, exist_ok=True)
    intent = dict(step=step, attempt=number, continuation_kind='fragment', context_mode=mode, **profile_binding,
                  task_ref=c.record_ref(root / 'jobs' / f'{step:03d}.json'),
                  root_intent_ref=c.record_ref(paths[0] / 'native-intent.json'), previous=execution.proof(last),
                  fragment_checkpoint=dict(path=(last / 'checkpoint.json').relative_to(root).as_posix()), budget_slice=anchor)
    if session_ref is not None:
        intent['session'] = session_ref
    else:
        intent['native_home'] = str(root / 'native-homes' / f'{step:03d}' / f'context-{number:03d}')
    from . import accepted_budget
    accepted_budget.accept_native(root,step,record,intent)
    runtime.start(root, step, number)
    return intent


def start(root, step, *, inventory=None, expected_attempt=None):
    from .protocol import require_current
    require_current(root)
    from . import contracts as _contracts
    require_current(root,_contracts.read(_contracts.regular(Path(root)/'jobs'/f'{step:03d}.json')))
    if (execution.origin(root, step) / 'result-identity.json').exists():
        return c.read(execution.origin(root,step)/'result.json')
    paths = execution.records(root, step)
    if runtime.node_state(root, step) == 'interrupted':
        reenter(root, step)
        return native.wait_native(root, step, _reentry_started=True)
    if runtime.node_state(root, step) in {'running','waiting','unstarted'} or (len(paths) > 1 and runtime.node_state(root, step) == 'unknown'):
        return native.wait_native(root, step)  # An accepted attempt is collected before cap checks.
    last = paths[-1]
    outcome = c.native_outcome(last)
    from . import native_recovery
    if native_recovery.never_started(outcome):
        return _start_technical_restart(root,step,paths,outcome=outcome,
            expected_attempt=expected_attempt)
    if expected_attempt is not None:
        if (type(expected_attempt) is not int or expected_attempt < 0
                or expected_attempt >= len(paths)-1
                or (c.read(c.regular(paths[expected_attempt+1]/'native-intent.json')).get('technical_restart') or {}).get('source_attempt') != expected_attempt):
            raise ResumeUnavailable('technical_restart_source_attempt_changed')
        return native.wait_native(root,step)
    if inventory is None:inventory=c.accept_current_inventory(root,step)
    job = inventory.job
    repair = None
    work_repair=False
    if outcome.get('returncode') == 0 and not outcome.get('stop_reason'):
        try:
            if checkpoint.enabled(root, job):
                if outcome.get('output_status') == 'missing':
                    raise OutputRepairRequired('missing_output', 'Return the missing scientific summary without rerunning completed work.')
                science=c.science_payload(last/'science-output.json','W',job.get('handoff_protocol',1),**c.response_options(job))
                from . import candidate_refs, worker_delivery, candidate_nominations
                science=candidate_refs.control_payload(job,science,root=root,inventory=inventory)
                if science['stage_intent']!='continue' or (last/'stage-end.json').exists():
                    worker_delivery.require_delivery(root,job,science,last,inventory=inventory)
                    candidate_nominations.require(root,job,science,inventory=inventory)
                if (last/'stage-end.json').exists():return c.seal_result_from_inventory(root, step,inventory=inventory)
                return native.wait_native(root,step,inventory=inventory)
            return c.seal_result_from_inventory(root, step,inventory=inventory)
        except WorkRepairRequired as error:
            inventory=error.inventory
            repair=output_request(root,step,last,inventory=inventory)
            work_repair=True
        except OutputRepairRequired:
            limits = validate_launcher(root, step)
            if not limits.get('output_repair', False):
                raise
            repair = output_request(root, step, last,inventory=inventory)
    limits = validate_launcher(root, step)
    closeout = budget.closeout_state(root)['begin']
    job = inventory.job
    if limits.get('output_only') and (repair is None or work_repair):
        raise ResumeUnavailable('worker_resume_output_only')
    final_repair = repair is not None and not work_repair and budget.final_a_barrier(root) is not None and budget.final_a_barrier(root)['step']==step
    if (root / 'pause.request').exists() or (closeout is not None and job.get('purpose') != 'closeout' and not final_repair):
        raise ResumeUnavailable('worker_resume_paused_or_closeout')
    from . import correction
    if correction.gate(root, step - 1) is not None:
        raise ResumeUnavailable('worker_resume_correction_paused')
    repairs = sum(c.read(path / 'native-intent.json').get('continuation_kind', 'repair') == 'repair' for path in paths[1:])
    if repairs >= limits['max_attempts']:raise ResumeUnavailable('worker_resume_limit')
    settings = configuration_updates.for_step(root, step)
    from . import fork_runtime
    for later in (number for number in fork_runtime.local_steps(root, include_native=True) if number > step):
        if (execution.origin(root, later) / 'native-intent.json').exists():
            raise ResumeUnavailable('worker_resume_successor_present')
    original_intent = c.read(c.regular(paths[0] / 'native-intent.json'))
    if original_intent.get('task_ref') != c.record_ref(root / 'jobs' / f'{step:03d}.json'):
        raise ValueError('Original native task identity changed')
    validate_task(root, step,inventory=inventory)
    _eligible_original(root, step, paths, successful=repair is not None, wait_runner=repair is not None)
    session_ref = session(root, step, last)
    from . import profiles
    profile_binding = profiles.binding(settings, job)
    profiles.validate_intent(settings, job, original_intent)
    number = len(paths)
    budget.transition(root, 'advance', 'explicit_worker_continuation')
    anchor = budget.accept_continuation(root, job, paths, output_repair=repair if not work_repair else None,inventory=inventory)
    record = execution.origin(root, step) / 'continuations' / f'{number:03d}'
    record.mkdir(parents=True, exist_ok=True)
    intent = dict(step=step, attempt=number, continuation_kind='repair', **profile_binding, task_ref=c.record_ref(root / 'jobs' / f'{step:03d}.json'),
                  root_intent_ref=c.record_ref(paths[0] / 'native-intent.json'), previous=execution.proof(last),
                  session=session_ref, budget_slice=anchor,
                  **({('work_repair' if work_repair else 'output_repair'):repair} if repair is not None else {}))
    validate_repair(root,step,intent,prior_record=last,
        prior_intents=[c.read(path/'native-intent.json') for path in paths],
        verify_task=False,inventory=inventory)
    from . import accepted_budget
    accepted_budget.accept_native(root,step,record,intent)
    runtime.start(root, step, number)
    return native.wait_native(root, step)


def validate_wake(root,step,intent,prior):
    from . import managed_wait, experiments
    job=c.read(root/'jobs'/f'{step:03d}.json')
    validate_wake_policy(root,step)
    if (job['role']!='W' or job.get('formal_review') or job.get('final_only')
            or job.get('final_report') or job.get('purpose')=='closeout'):
        raise ValueError('Execution wake requires the original ordinary W task')
    source=c.read(c.regular(prior/'native-intent.json'))
    repair=source.get('output_repair') is not None
    if (source.get('continuation_kind')=='author_closeout'
            or repair and (type(source.get('attempt')) is not int or source['attempt']<1
                or not checkpoint.enabled(root,job))):
        raise ValueError('Execution wake cannot expand a special continuation scope')
    value=managed_wait.validate(root,job,prior)
    if repair and c.read(c.regular(prior/'checkpoint.json'))['science']['status']!='present':
        raise ValueError('Execution wake requires a successfully completed ordinary W output repair')
    request=intent['execution_wake']
    if request['waiting']!=experiments.proof(prior/managed_wait.NAME):
        raise ValueError('Execution wake changed its original waiting checkpoint')
    expected=[]
    for item in value['calls']:
        path=Path(item['accepted']['path']).parent/'execution-observation.json'
        observed=c.read(c.regular(path))
        accepted=c.read(Path(item['accepted']['path']))
        # Independent ordinary-stage permission may consume an older accepted
        # original after JSON repair; it grants no execution in that repair turn.
        if repair and (type(accepted.get('native_attempt',{}).get('number')) is not int
                or not 0<=accepted['native_attempt']['number']<source['attempt']):
            raise ValueError('Execution wake after output repair requires previously accepted ordinary work')
        _,config=managed_wait._config(root,job,accepted)
        experiments.verify_observation(observed,lambda proof:Path(proof['path']),interface=config['execution_interface'])
        expected.append(experiments.proof(path))
    if request['observations']!=expected:raise ValueError('Execution wake differs from the same original terminal observations')


def wake_start(root,step,observations,*,inventory=None):
    """Resume one successful suspended native turn, sharing original finite authorization."""
    from . import managed_wait, experiments
    validate_wake_policy(root, step);job=validate_task(root,step,inventory=inventory);paths=execution.records(root,step);last=paths[-1]
    if inventory is None:
        from . import handoff_state
        job = handoff_state.material_inputs(root,job,sources={})
    if (execution.origin(root,step)/'result-identity.json').exists():raise ResumeUnavailable('execution_wake_stage_already_sealed')
    if (root/'pause.request').exists() or budget.closeout_state(root)['begin'] is not None:
        raise ResumeUnavailable('worker_resume_paused_or_closeout')
    from . import correction
    if correction.gate(root,step-1) is not None:raise ResumeUnavailable('worker_resume_correction_paused')
    from . import fork_runtime
    if any((execution.origin(root,later)/'native-intent.json').exists()
            for later in fork_runtime.local_steps(root, include_native=True) if later > step):
        raise ResumeUnavailable('worker_resume_successor_present')
    request=dict(waiting=experiments.proof(last/managed_wait.NAME),observations=observations)
    # Check ordinary-W scope and each exact terminal original before accepting
    # another dispatch or advancing any cumulative budget anchor.
    validate_wake(root,step,dict(execution_wake=request),last)
    _eligible_original(root,step,paths,successful=True,wait_runner=True)
    session_ref=session(root,step,last)
    from . import profiles
    profile_binding=profiles.binding(configuration_updates.for_step(root, step),job)
    profiles.validate_intent(configuration_updates.for_step(root, step),job,c.read(paths[0]/'native-intent.json'))
    number=len(paths)
    budget.transition(root,'advance','accepted_execution_wake')
    anchor=budget.accept_continuation(root,job,paths)
    record=execution.origin(root,step)/'continuations'/f'{number:03d}'
    record.mkdir(parents=True,exist_ok=True)
    intent=dict(step=step,attempt=number,continuation_kind='execution_wake',**profile_binding,
        task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'),root_intent_ref=c.record_ref(paths[0]/'native-intent.json'),
        previous=execution.proof(last),session=session_ref,budget_slice=anchor,execution_wake=request)
    from . import accepted_budget
    accepted_budget.accept_native(root,step,record,intent)
    runtime.start(root,step,number)
    return intent
