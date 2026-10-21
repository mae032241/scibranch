"""Explicit finite continuation of a known local failure, never a fresh replay."""
import json
import hashlib
import os
import time
from pathlib import Path
import uuid

from . import configuration_updates
from . import budget, contracts as c, execution, native, runtime, checkpoint
from .output_repair import OutputRepairRequired, WorkRepairRequired


class ResumeUnavailable(ValueError):
    pass


def policy(value, launcher):
    required={'max_attempts','protocol','usage_scope'}
    if (not isinstance(value, dict) or not required <= set(value)
            or set(value)-required-{'output_repair','output_only','native_home'}
            or type(value['max_attempts']) is not int or not 1 <= value['max_attempts'] <= 2
            or value['protocol'] != 'codex_exec_resume' or value['usage_scope'] != 'turn'
            or ('output_repair' in value and type(value['output_repair']) is not bool)
            or ('output_only' in value and (type(value['output_only']) is not bool
                or value['output_only'] and not value.get('output_repair',False)))
            or ('native_home' in value and (not value.get('output_only') or not isinstance(value['native_home'],str)
                or not Path(value['native_home']).is_absolute() or Path(value['native_home']).resolve()!=Path(value['native_home'])))):
        raise ValueError('Resume requires explicit finite attempts and qualified turn-scoped launcher protocol')
    if not launcher or not Path(launcher[0]).is_absolute() or not Path(launcher[0]).is_file():
        raise ValueError('Resume requires an exact frozen executable, never PATH selection')
    files = []
    for argument in launcher:
        path = Path(argument).absolute()
        if path.is_file():
            original = c.regular(path.resolve())
            files.append(dict(invoked_path=str(path), path=str(original)))
    if not files:raise ValueError('Resume-capable launcher originals must be frozen')
    return dict(value, launcher_files=files)


def validate_launcher(root, step=None):
    from . import backends
    settings = configuration_updates.for_step(root, step) if step is not None else configuration_updates.current(root)
    if not backends.selected(settings).supports_resume:
        raise ResumeUnavailable('worker_resume_unsupported_backend')
    limits = settings.get('resume_policy')
    if limits is None:raise ResumeUnavailable('worker_resume_unsupported')
    for proof in limits['launcher_files']:
        if Path(proof['invoked_path']).resolve() != Path(proof['path']):
            raise ValueError('Frozen resume launcher changed')
        c.regular(Path(proof['path']))
    return limits



def validate_task(root, step):
    job = c.read(c.regular(root / 'jobs' / f'{step:03d}.json'))
    c._validate_inputs(root, c.workspace(root, step), job)
    schema = c.read(c.regular(execution.origin(root, step) / 'science-result.schema.json', limit=None))
    expected = c.result_schema(job['role'], job.get('handoff_protocol', 1), 'selection' in job,**c.response_options(job))
    if schema != expected:
        # The previous same-protocol schema included this framework storage cap.
        # Accept only that exact prior shape; retain the frozen schema bytes.
        prior = json.loads(json.dumps(expected))
        prior['properties']['implementation_selection']['maxItems'] = 16
        if schema != prior:
            raise ValueError('Original native output schema changed')
    return job

def validate_session_header(line, thread, workspace):
    event = json.loads(line)
    if (not isinstance(event,dict) or not isinstance(event.get('payload'),dict)
            or event.get('type') != 'session_meta' or event.get('payload', {}).get('id') != thread
            or event['payload'].get('cwd') != str(workspace)):
        raise ValueError('Native session identity or workspace changed')


def session(root, step, record, *, home=None, session_path=None, identity_proof=False):
    from . import backends
    if not backends.selected(configuration_updates.for_step(root, step)).supports_resume:
        raise ResumeUnavailable('worker_resume_unsupported_backend')
    outcome = c.read(c.regular(record / 'native-outcome.json'))
    thread = outcome.get('thread_id')
    try:
        if str(uuid.UUID(thread)) != thread:raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ResumeUnavailable('worker_resume_thread_unavailable')
    found = False
    with c.regular(record / 'native.jsonl').open('rb') as stream:
        for line in stream:
            event = json.loads(line)
            if event.get('type') == 'thread.started':
                if event.get('thread_id') != thread:raise ValueError('Native thread identity changed')
                thread_event_sha256=hashlib.sha256(line).hexdigest()
                found = True
                break
    if not found:raise ResumeUnavailable('worker_resume_thread_unavailable')
    number = execution.records(root, step).index(record)
    home = Path(home) if home is not None else execution.home(root, step, number)
    if home.resolve() != home:raise ValueError('Original native home changed')
    matches = []
    paths = [Path(session_path)] if session_path is not None else (home / 'sessions').rglob('*.jsonl')
    for path in paths:
        if path.resolve() != path or not path.is_relative_to(home / 'sessions'):
            raise ValueError('Native session original changed')
        with c.regular(path).open('rb') as stream:line = stream.readline()
        event = json.loads(line)
        if event.get('type') == 'session_meta' and event.get('payload', {}).get('id') == thread:
            validate_session_header(line, thread, c.workspace(root, step))
            matches.append(dict(path=str(path), header_sha256=hashlib.sha256(line).hexdigest()))
    if len(matches) != 1:raise ResumeUnavailable('worker_resume_session_unavailable')
    return dict(thread_id=thread, home=str(home), workspace=str(c.workspace(root, step)),
                **(dict(thread_event_sha256=thread_event_sha256) if identity_proof else {}), **matches[0])



def _work_exit(root,job,record,reason):
    from . import worker_delivery
    paths=execution.records(root,job['step'])
    _eligible_original(root,job['step'],paths,successful=True,wait_runner=True,
        budgeted=budget.node_limits(root,job) is not None)
    proof=worker_delivery.authorize_exit(root,job,record,reason)
    if not (record/'checkpoint.json').exists():checkpoint.capture(root,job,record)
    checkpoint.end_stage(root,job,record,reason,work_exit=proof)
    return c.seal_result(root,job['step'])


def seal_or_repair(root, step):
    """Try the original seal first; only a frozen opt-in can accept a repair turn."""
    from .protocol import require_current
    require_current(root)
    from . import contracts as _contracts
    require_current(root,_contracts.read(_contracts.regular(Path(root)/'jobs'/f'{step:03d}.json')))
    try:
        job = c.read(c.regular(root / 'jobs' / f'{step:03d}.json'))
        if not (execution.origin(root,step)/'result-identity.json').exists():
            from . import managed_wait
            managed_wait.before_seal(root,job,execution.record(root,step))
        if checkpoint.enabled(root, job) and not (execution.origin(root, step) / 'result-identity.json').exists():
            record = execution.record(root, step)
            if (record / 'stage-end.json').exists():
                checkpoint.stage_end(root, job, record)
                return c.seal_result(root, step)
            saved = checkpoint.validate(root, job, record)
            outcome = c.read(c.regular(record / 'native-outcome.json'))
            if (outcome.get('returncode') == 0 and not outcome.get('stop_reason')
                    and (not outcome['usage']['unknown_steps'] or budget.native_completion_known(outcome, configuration_updates.for_step(root, step)))):
                if saved['science']['status'] != 'present':
                    # c.seal_result provides the bound output diagnosis/repair.
                    return c.seal_result(root, step)
                science = c.science_payload(record / 'science-output.json', 'W', job.get('handoff_protocol', 1))
                review = fragment_review(root, job)
                review_required = review['requested'] and review['enforced']
                from . import worker_delivery
                delivery=worker_delivery.assessment(root,job,science,record)
                if delivery['status']=='partial':
                    checkpoint.end_stage(root,job,record,delivery['reason'])
                    return c.seal_result(root,step)
                if delivery['status']=='incomplete' and review_required:
                    return _work_exit(root,job,record,review['reason'])
                if science['stage_intent']!='continue':worker_delivery.require_delivery(root,job,science,record)
                if checkpoint.disposition(science, review_requested=review_required) == 'continue':
                    try:
                        fragment_start(root, step)
                    except budget.NodeBudgetUnavailable as error:
                        # A valid scientific contribution is retained at the
                        # original budget boundary, without another native turn.
                        if delivery['status']=='incomplete':return _work_exit(root,job,record,error.reason)
                        checkpoint.end_stage(root, job, record, error.reason)
                        return c.seal_result(root, step)
                    except ResumeUnavailable as error:
                        if str(error) != 'worker_fragment_review_requested':
                            raise
                        if delivery['status']=='incomplete':return _work_exit(root,job,record,error.review_reason)
                        checkpoint.end_stage(root, job, record, error.review_reason)
                        return c.seal_result(root, step)
                    return dict(fragment_continuation=True)
                checkpoint.end_stage(root, job, record, review['reason'] if review_required else 'worker_requested')
        return c.seal_result(root, step)
    except WorkRepairRequired:
        job=c.read(c.regular(root/'jobs'/f'{step:03d}.json'));record=execution.record(root,step)
        try:return start(root,step)
        except budget.NodeBudgetUnavailable as error:return _work_exit(root,job,record,error.reason)
        except ResumeUnavailable as error:
            return _work_exit(root,job,record,str(error))
    except OutputRepairRequired:
        if not (configuration_updates.for_step(root, step).get('resume_policy') or {}).get('output_repair', False):
            raise
        return start(root, step)


def output_request(root, step, record, *, source_attempt=None, verify_task=True):
    """Recompute a repair binding from verified original final-response bytes."""
    job = validate_task(root, step) if verify_task else c.read(c.regular(root / 'jobs' / f'{step:03d}.json'))
    outcome = c.read(c.regular(record / 'native-outcome.json'))
    if (type(outcome.get('returncode')) is not int or outcome['returncode'] != 0
            or outcome.get('stop_reason')
            or outcome['usage']['unknown_steps'] and not budget.native_completion_known(outcome, configuration_updates.for_step(root, step))):
        raise ValueError('Output repair requires a known completed original turn')
    raw = record / 'science-output.json'
    if outcome.get('output_status') == 'missing':
        if raw.exists() or raw.is_symlink() or (record / 'result.pending.json').exists() or (record / 'result.pending.json').is_symlink():
            raise ValueError('Missing final output identity changed')
        error = OutputRepairRequired('missing_output', 'Return the required final scientific JSON; the completed work and checkpoints are already retained.')
    else:
        if c.digest(c.regular(raw, limit=None)) != outcome.get('raw_sha256'):
            raise ValueError('Original final output changed before repair')
        try:
            science=c.science_payload(raw, job['role'], job.get('handoff_protocol', 1), 'selection' in job,**c.response_options(job))
            from . import candidate_refs
            science=candidate_refs.control_payload(job,science,root=root)
            from . import formal_review
            formal_review.validate_choice(root,job,science)
            if job['role']=='W':
                from . import worker_delivery, candidate_nominations
                worker_delivery.require_delivery(root,job,science,record)
                candidate_nominations.require(root,job,science)
        except OutputRepairRequired as failure:
            error = failure
        else:
            raise ValueError('A valid final response does not authorize an output repair')
    if source_attempt is None:
        source_attempt = len(execution.records(root, step)) - 1
    return dict(**error.request(), source_attempt=source_attempt,
                outcome_sha256=c.digest(record / 'native-outcome.json'), raw_sha256=outcome.get('raw_sha256'),
                **(dict(work_evidence_sha256=c.digest(record/'work-evidence.json')) if isinstance(error,WorkRepairRequired) else {}))


def validate_repair(root, step, intent, *, prior_record=None, verify_task=True):
    request = intent.get('work_repair') or intent.get('output_repair')
    if request is None:
        return
    if (configuration_updates.for_step(root, step).get('resume_policy') or {}).get('output_only') and intent.get('work_repair') is not None:
        raise ValueError('Output-only policy does not authorize scientific work repair')
    if intent.get('work_repair') is None and not validate_launcher(root, step).get('output_repair', False):
        raise ValueError('Output repair is not authorized by the frozen policy')
    prior = prior_record if prior_record is not None else execution.records(root, step)[intent['attempt'] - 1]
    expected = output_request(root, step, prior, source_attempt=intent['attempt'] - 1, verify_task=verify_task)
    if request != expected:
        raise ValueError('Accepted output repair differs from its original response failure')


def validate_repair_chain(root, step, chain):
    """Terminal execution checks every repair source without replaying projections."""
    for number, record in enumerate(chain[1:], 1):
        intent = c.read(c.regular(record / 'native-intent.json'))
        if intent.get('output_repair') is not None or intent.get('work_repair') is not None:
            # execution.records already verified task/attempt/previous identities.
            # Avoid terminal -> scientific projection -> terminal recursion.
            validate_repair(root, step, intent, prior_record=chain[number - 1], verify_task=False)


def validate_fragment_chain(root, step, chain):
    job = c.read(c.regular(root / 'jobs' / f'{step:03d}.json'))
    for number, record in enumerate(chain[1:], 1):
        intent = c.read(c.regular(record / 'native-intent.json'))
        if intent.get('continuation_kind') != 'fragment':
            continue
        prior = chain[number - 1]
        saved = checkpoint.validate(root, job, prior)
        original = c.read(c.regular(prior / 'native-outcome.json'))
        science = c.science_payload(prior / 'science-output.json', 'W', job.get('handoff_protocol', 1))
        if (saved['science']['status'] != 'present' or science.get('stage_intent') != 'continue'
                or original.get('returncode') != 0 or original.get('stop_reason')
                or original['usage']['unknown_steps'] and not budget.native_completion_known(original, configuration_updates.for_step(root, step))):
            raise ValueError('Fragment continuation has no qualified original continuing turn')
        mode = intent.get('context_mode')
        if mode != checkpoint.policy(configuration_updates.for_step(root, step)['worker_fragment_policy'])['context']:
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
    outcome = c.read(c.regular(last / 'native-outcome.json'))
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
        if c.digest(c.regular(last / name)) != outcome.get('execution_processes', {}).get(name):
            raise ValueError('Original execution process receipt changed')
        identity = c.read(c.regular(last / name))
        if (identity.get('task_sha256') != binding or identity.get('boot_id') != budget.clock_stamp()['boot_id']
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


def fragment_start(root, step):
    """Accept the next successful W fragment, never a failure retry or new job."""
    from .protocol import require_current
    require_current(root)
    from . import contracts as _contracts
    require_current(root,_contracts.read(_contracts.regular(Path(root)/'jobs'/f'{step:03d}.json')))
    root = Path(root)
    if validate_launcher(root, step).get('output_only'):raise ResumeUnavailable('worker_resume_output_only')
    job = validate_task(root, step)
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
    for later in range(step + 1, budget.historical_step_limit(root) + 1):
        if (execution.origin(root, later) / 'native-intent.json').exists():
            raise ResumeUnavailable('worker_resume_successor_present')
    paths = execution.records(root, step)
    last = paths[-1]
    saved = checkpoint.validate(root, job, last)
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
    session_ref = session(root, step, last) if mode == 'same_session' else None
    from . import profiles
    profile_binding = profiles.binding(settings, job)
    profiles.validate_intent(settings, job, c.read(paths[0] / 'native-intent.json'))
    # All previous targets and shared authorization are cumulatively consumed.
    budget.transition(root, 'advance', 'accepted_worker_fragment')
    anchor = budget.accept_continuation(root, job, paths)
    number = len(paths)
    record = execution.origin(root, step) / 'continuations' / f'{number:03d}'
    record.mkdir(parents=True, exist_ok=True)
    intent = dict(step=step, attempt=number, continuation_kind='fragment', context_mode=mode, **profile_binding,
                  task_sha256=c.digest(root / 'jobs' / f'{step:03d}.json'),
                  root_intent_sha256=c.digest(paths[0] / 'native-intent.json'), previous=execution.proof(last),
                  fragment_checkpoint=dict(path=(last / 'checkpoint.json').relative_to(root).as_posix(),
                                           sha256=c.digest(last / 'checkpoint.json')), budget_slice=anchor)
    if session_ref is not None:
        intent['session'] = session_ref
    else:
        intent['native_home'] = str(root / 'native-homes' / f'{step:03d}' / f'context-{number:03d}')
    with (record / 'native-intent.json').open('x') as stream:
        json.dump(intent, stream);stream.flush();os.fsync(stream.fileno())
    runtime.start(root, step, number)
    return intent


def start(root, step):
    from .protocol import require_current
    require_current(root)
    from . import contracts as _contracts
    require_current(root,_contracts.read(_contracts.regular(Path(root)/'jobs'/f'{step:03d}.json')))
    limits = validate_launcher(root, step)
    if (execution.origin(root, step) / 'result-identity.json').exists():
        return c.validate(c.workspace(root, step))
    paths = execution.records(root, step)
    if runtime.node_state(root, step) in {'running','waiting'} or (len(paths) > 1 and runtime.node_state(root, step) == 'unknown'):
        return native.wait_native(root, step)  # An accepted attempt is collected before cap checks.
    last = paths[-1]
    outcome = c.read(c.regular(last / 'native-outcome.json'))
    repair = None
    work_repair=False
    if outcome.get('returncode') == 0 and not outcome.get('stop_reason'):
        try:
            if checkpoint.enabled(root, c.read(root / 'jobs' / f'{step:03d}.json')):
                if outcome.get('output_status') == 'missing':
                    raise OutputRepairRequired('missing_output', 'Return the missing scientific summary without rerunning completed work.')
                job=c.read(root/'jobs'/f'{step:03d}.json')
                science=c.science_payload(last/'science-output.json','W',job.get('handoff_protocol',1),**c.response_options(job))
                from . import candidate_refs, worker_delivery, candidate_nominations
                science=candidate_refs.control_payload(job,science,root=root)
                if science['stage_intent']!='continue' or (last/'stage-end.json').exists():
                    worker_delivery.require_delivery(root,job,science,last)
                    candidate_nominations.require(root,job,science)
                return native.wait_native(root,step)
            return c.seal_result(root, step)
        except WorkRepairRequired:
            repair=output_request(root,step,last)
            work_repair=True
        except OutputRepairRequired:
            if not limits.get('output_repair', False):
                raise
            repair = output_request(root, step, last)
    closeout = budget.closeout_state(root)['begin']
    job = c.read(c.regular(root / 'jobs' / f'{step:03d}.json'))
    if limits.get('output_only') and (repair is None or work_repair):
        raise ResumeUnavailable('worker_resume_output_only')
    final_repair = repair is not None and not work_repair and budget.final_a_barrier(root) is not None and budget.final_a_barrier(root)['step']==step
    if (root / 'pause.request').exists() or (closeout is not None and job.get('purpose') != 'closeout' and not final_repair):
        raise ResumeUnavailable('worker_resume_paused_or_closeout')
    from . import correction
    if correction.gate(root, step - 1) is not None:
        raise ResumeUnavailable('worker_resume_correction_paused')
    repairs = sum(c.read(path / 'native-intent.json').get('continuation_kind', 'repair') != 'fragment' for path in paths[1:])
    if repairs >= limits['max_attempts']:raise ResumeUnavailable('worker_resume_limit')
    settings = configuration_updates.for_step(root, step)
    for later in range(step + 1, budget.historical_step_limit(root) + 1):
        if (execution.origin(root, later) / 'native-intent.json').exists():
            raise ResumeUnavailable('worker_resume_successor_present')
    original_intent = c.read(c.regular(paths[0] / 'native-intent.json'))
    if original_intent.get('task_sha256') != c.digest(root / 'jobs' / f'{step:03d}.json'):
        raise ValueError('Original native task identity changed')
    validate_task(root, step)
    _eligible_original(root, step, paths, successful=repair is not None, wait_runner=repair is not None)
    session_ref = session(root, step, last)
    from . import profiles
    profile_binding = profiles.binding(settings, job)
    profiles.validate_intent(settings, job, original_intent)
    number = len(paths)
    budget.transition(root, 'advance', 'explicit_worker_continuation')
    anchor = budget.accept_continuation(root, job, paths, output_repair=repair if not work_repair else None)
    record = execution.origin(root, step) / 'continuations' / f'{number:03d}'
    record.mkdir(parents=True, exist_ok=True)
    intent = dict(step=step, attempt=number, continuation_kind='repair', **profile_binding, task_sha256=c.digest(root / 'jobs' / f'{step:03d}.json'),
                  root_intent_sha256=c.digest(paths[0] / 'native-intent.json'), previous=execution.proof(last),
                  session=session_ref, budget_slice=anchor,
                  **({('work_repair' if work_repair else 'output_repair'):repair} if repair is not None else {}))
    with (record / 'native-intent.json').open('x') as stream:
        json.dump(intent, stream);stream.flush();os.fsync(stream.fileno())
    runtime.start(root, step, number)
    return native.wait_native(root, step)


def validate_wake(root,step,intent,prior):
    from . import managed_wait, experiments
    job=c.read(root/'jobs'/f'{step:03d}.json')
    managed_wait.validate(root,job,prior)
    request=intent['execution_wake']
    if request['waiting']!=experiments.proof(prior/managed_wait.NAME):
        raise ValueError('Execution wake changed its original waiting checkpoint')
    value=c.read(prior/managed_wait.NAME)
    expected=[]
    for item in value['calls']:
        path=Path(item['accepted']['path']).parent/'execution-observation.json'
        observed=c.read(c.regular(path))
        accepted=c.read(Path(item['accepted']['path']));_,config=managed_wait._config(root,job,accepted)
        experiments.verify_observation(observed,lambda proof:Path(proof['path']),interface=config['execution_interface'])
        expected.append(experiments.proof(path))
    if request['observations']!=expected:raise ValueError('Execution wake differs from the same original terminal observations')


def wake_start(root,step,observations):
    """Resume one successful suspended native turn, sharing original finite authorization."""
    from . import managed_wait, experiments
    limits=validate_launcher(root, step);job=validate_task(root,step);paths=execution.records(root,step);last=paths[-1]
    if limits.get('output_only'):raise ResumeUnavailable('worker_resume_output_only')
    if (execution.origin(root,step)/'result-identity.json').exists():raise ResumeUnavailable('execution_wake_stage_already_sealed')
    if (root/'pause.request').exists() or budget.closeout_state(root)['begin'] is not None:
        raise ResumeUnavailable('worker_resume_paused_or_closeout')
    from . import correction
    if correction.gate(root,step-1) is not None:raise ResumeUnavailable('worker_resume_correction_paused')
    if any((execution.origin(root,later)/'native-intent.json').exists() for later in range(step+1,budget.historical_step_limit(root)+1)):
        raise ResumeUnavailable('worker_resume_successor_present')
    attempts=sum(c.read(path/'native-intent.json').get('continuation_kind','repair')!='fragment' for path in paths[1:])
    if attempts>=limits['max_attempts']:raise ResumeUnavailable('worker_resume_limit')
    _eligible_original(root,step,paths,successful=True,wait_runner=True)
    session_ref=session(root,step,last)
    from . import profiles
    profile_binding=profiles.binding(configuration_updates.for_step(root, step),job)
    profiles.validate_intent(configuration_updates.for_step(root, step),job,c.read(paths[0]/'native-intent.json'))
    request=dict(waiting=experiments.proof(last/managed_wait.NAME),observations=observations)
    number=len(paths)
    budget.transition(root,'advance','accepted_execution_wake')
    anchor=budget.accept_continuation(root,job,paths)
    record=execution.origin(root,step)/'continuations'/f'{number:03d}'
    record.mkdir(parents=True,exist_ok=True)
    intent=dict(step=step,attempt=number,continuation_kind='execution_wake',**profile_binding,
        task_sha256=c.digest(root/'jobs'/f'{step:03d}.json'),root_intent_sha256=c.digest(paths[0]/'native-intent.json'),
        previous=execution.proof(last),session=session_ref,budget_slice=anchor,execution_wake=request)
    validate_wake(root,step,intent,last)
    with (record/'native-intent.json').open('x') as stream:
        json.dump(intent,stream);stream.flush();os.fsync(stream.fileno())
    runtime.start(root,step,number)
    return intent
