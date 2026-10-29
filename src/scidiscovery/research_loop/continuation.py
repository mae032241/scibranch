"""Explicit finite continuation of a known local failure, never a fresh replay."""
import json
import os
import time
from pathlib import Path
import uuid

from . import configuration_updates
from . import budget, contracts as c, execution, native, runtime, checkpoint
from .output_repair import OutputRepairRequired, WorkRepairRequired


class ResumeUnavailable(ValueError):
    pass


class AutomaticRecoveryRequired(RuntimeError):
    """Return one finite recovery decision to the owning controller writer."""
    def __init__(self, decision):
        super().__init__(decision['reason'])
        self.decision = decision


def automatic_source(root, step, record):
    """Verify the failure projection against the exact retained native JSONL."""
    from . import backends, native_recovery
    backend = backends.selected(configuration_updates.for_step(root, step))
    if backend.name != 'codex' or not backend.standard:
        raise ResumeUnavailable('automatic_recovery_unsupported_backend')
    outcome = c.read(c.regular(record / 'native-outcome.json'))
    failures = []
    terminal = activity = False
    original = c.read(c.regular(record / 'native-intent.json'))
    thread = (original.get('session') or {}).get('thread_id')
    with c.regular(record / 'native.jsonl', limit=None).open('rb') as stream:
        for line in stream:
            try:event = json.loads(line)
            except ValueError:continue  # A truncated transport tail grants nothing.
            if not isinstance(event, dict):continue
            projected = backend.event(event)
            if projected.get('failure') is not None:failures.append(projected['failure'])
            terminal = terminal or projected.get('completed', False)
            activity = activity or projected.get('activity', False)
            if 'session' in projected:thread = projected['session']
    expected = native_recovery.combine(failures)
    if (outcome.get('native_failure') != expected
            or outcome.get('terminal_observed') is not terminal
            or outcome.get('native_activity_observed') is not activity
            or outcome.get('thread_id') != thread):
        raise ValueError('Automatic recovery native failure evidence changed')
    return outcome


def format_source(root, step, record):
    """Verify pre-science format rejection from originals, not saved diagnostics.

    Older receipts may retain an unknown error projection. That projection is
    never authority for negotiation; the complete raw native stream supplies
    the positive request rejection, with every other retained fact checked.
    """
    from . import backends, native_recovery
    backend = backends.selected(configuration_updates.for_step(root, step))
    if backend.name != 'codex' or not backend.standard:return None
    outcome = c.read(c.regular(record / 'native-outcome.json'))
    if (outcome.get('terminal_observed') is not False or type(outcome.get('returncode')) is not int
            or outcome['returncode'] == 0 or outcome.get('stop_reason') not in {None,'native_failure_event'}
            or outcome.get('cleanup') != 'observed_group_cleaned'
            or outcome.get('native_schema_transport', 'native_flag') != 'native_flag'):
        return None
    original = c.read(c.regular(record / 'native-intent.json'))
    thread = (original.get('session') or {}).get('thread_id')
    activity = rejected = False
    raw=c.regular(record / 'native.jsonl', limit=None)
    if raw.stat().st_size > 256 * 1024:return None
    with raw.open('rb') as stream:
        for number in range(65):
            line=stream.readline(32 * 1024 + 1)
            if not line:break
            if number >= 64 or len(line) > 32 * 1024:return None
            try:event=json.loads(line)
            except (ValueError,RecursionError):return None
            if not isinstance(event,dict):return None
            kind=event.get('type')
            if kind in {'error','turn.failed'}:
                if not native_recovery.format_rejection(event):return None
                rejected=True
            elif kind not in {'thread.started','turn.started'} or rejected:
                return None
            projected=backend.event(event)
            activity=activity or projected.get('activity',False)
            if 'session' in projected:
                if thread is not None and thread != projected['session']:return None
                thread=projected['session']
    if not rejected:return None
    if outcome.get('native_activity_observed') is not activity or outcome.get('thread_id') != thread:
        raise ValueError('Automatic recovery native format evidence changed')
    return dict(outcome, format_rejected=True)


def _automatic_session(root, step, record, outcome):
    identity = outcome.get('native_identity') or {}
    path = identity.get('path')
    original = c.read(c.regular(record / 'native-intent.json'))
    retained = original.get('session') or {}
    if retained and outcome.get('thread_id') == retained.get('thread_id'):
        path = Path(retained['path'])
        if (path.resolve() != path or not path.is_relative_to(Path(retained['home']) / 'sessions')
                or retained.get('workspace') != str(c.workspace(root, step))):
            raise ValueError('Automatic recovery retained session identity changed')
        with c.regular(path, limit=None).open('rb') as stream:header = stream.readline()
        validate_session_header(header, retained['thread_id'], c.workspace(root, step))
        if json.loads(header) != retained.get('header'):
            raise ValueError('Original native session header changed')
        return retained
    if not path and retained:
        path = retained.get('path')
    if not path:
        raise ResumeUnavailable('worker_resume_session_unavailable')
    return session(root, step, record, home=identity.get('home') or retained.get('home'), session_path=path)


def _completed_work_request(root, step, record, source_attempt, prior_intents):
    """Only the existing exact W delivery contract can reopen completed work."""
    job = c.read(c.regular(root / 'jobs' / f'{step:03d}.json'))
    if (job['role'] != 'W' or job.get('formal_review') or job.get('final_only')
            or job.get('purpose') == 'closeout'
            or any(item.get('continuation_kind') in {'author_closeout','execution_wake'}
                or item.get('execution_wake')
                for item in prior_intents)
            or not (record / 'work-evidence.json').exists() or (record / 'stage-end.json').exists()):
        return None
    from . import worker_delivery
    if not worker_delivery.repair_applicable(job):return None
    outcome = c.read(c.regular(record / 'native-outcome.json'))
    if (outcome.get('returncode') != 0 or outcome.get('stop_reason')
            or outcome.get('output_status') == 'missing'
            or outcome.get('usage', {}).get('unknown_steps') and not budget.native_completion_known(
                outcome, configuration_updates.for_step(root, step))):
        return None
    try:
        science = c.science_payload(record / 'science-output.json', 'W', job.get('handoff_protocol', 1),
            **c.response_options(job))
        # A valid continuation fragment retains its original fragment route.
        if science.get('stage_intent') == 'continue':return None
        if worker_delivery.assessment(root,job,science,record,source_attempt=source_attempt)['status']!='incomplete':
            return None
        request = output_request(root, step, record, source_attempt=source_attempt, verify_task=False)
    except OutputRepairRequired:
        return None
    return request if request['code'] == 'worker_delivery_incomplete' else None


def automatic_decision(root, step):
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
    outcome = c.read(c.regular(last / 'native-outcome.json'))
    completed = outcome.get('terminal_observed') is True
    work_request = None
    if completed:
        work_request = _completed_work_request(root, step, last, len(paths)-1,
            [c.read(c.regular(path / 'native-intent.json')) for path in paths])
        if work_request is None:return dict(base, reason='automatic_recovery_completed_turn')
        if checkpoint.enabled(root,job):
            review=fragment_review(root,job)
            if review['requested'] and review['enforced']:
                return dict(base, reason='worker_fragment_review_requested')
    original = c.read(c.regular(last / 'native-intent.json'))
    format_outcome = (format_source(root, step, last)
        if not completed and (last / 'native.jsonl').exists() else None)
    if (original.get('continuation_kind') in {'author_closeout','execution_wake'}
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
    if backend.name != 'codex' or not backend.standard:return stop('automatic_recovery_unsupported_backend')
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
    if job['role'] == 'W' and any(row['status'] not in {'known_success','known_failed','not_submitted'}
            for row in history.managed_calls(root, job)):
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


def validate_automatic(root, step, intent, *, prior_record=None, prior_intents=None):
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
        work_request=_completed_work_request(root,step,prior_record,intent['attempt']-1,prior_intents)
    if work_request is not None:
        if (outcome.get('terminal_observed') is not True or (prior_record/'stage-end.json').exists()
                or any(item.get('continuation_kind') in {'author_closeout','execution_wake'}
                    or item.get('execution_wake') for item in prior_intents)):
            raise ValueError('Automatic work recovery cannot reopen a closed or special task')
        expected = native_recovery.work_decision(limits, native_recovery.counters(prior_intents),
            session_available=bool(intent.get('session')))
        if intent.get('work_repair') != work_request or intent.get('output_repair') is not None:
            raise ValueError('Automatic work recovery differs from its exact original delivery failure')
    elif not format_fallback and (job.get('final_only') or job.get('purpose') == 'closeout'
            or prior_intent.get('continuation_kind') in {'author_closeout','execution_wake'}
            or prior_intent.get('output_repair')
            or prior_intent.get('work_repair') and prior_intent.get('continuation_kind')!='automatic_recovery'
            or intent.get('work_repair') or intent.get('output_repair')
            or not native_recovery.recoverable_source(outcome)):
        raise ValueError('Automatic recovery source is not an eligible unfinished task')
    used = native_recovery.counters(prior_intents)
    if format_fallback:
        if (prior_intent.get('continuation_kind') in {'author_closeout','execution_wake'}
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


def automatic_start(root, step, decision, *, recovery_wait=None):
    with budget._operator_writer(root):
        return _automatic_start_locked(root, step, decision, recovery_wait=recovery_wait)


def _automatic_start_locked(root, step, decision, *, recovery_wait=None):
    """Accept one replacement under the existing owner/controller writer."""
    from . import profiles, native_recovery
    root = Path(root)
    current = automatic_decision(root, step)
    if current != decision or current['action'] not in {'continue','retry'}:
        raise ResumeUnavailable('automatic_recovery_decision_stale')
    validate_launcher(root, step)
    job = validate_task(root, step)
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
    anchor = budget.accept_continuation(root, job, paths, automatic_recovery=current)
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
        prior_intents=[c.read(c.regular(path / 'native-intent.json')) for path in paths])
    with (record / 'native-intent.json').open('x') as stream:
        json.dump(intent, stream);stream.flush();os.fsync(stream.fileno())
    runtime.start(root, step, number)
    return intent


def policy(value, launcher):
    required={'max_attempts','protocol','usage_scope'}
    if (not isinstance(value, dict) or not required <= set(value)
            or set(value)-required-{'output_repair','output_only','execution_wake','native_home','automatic_recovery'}
            or type(value['max_attempts']) is not int or not 1 <= value['max_attempts'] <= 2
            or value['protocol'] != 'codex_exec_resume' or value['usage_scope'] != 'turn'
            or ('output_repair' in value and type(value['output_repair']) is not bool)
            or ('execution_wake' in value and type(value['execution_wake']) is not bool)
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
    result = dict(value, launcher_files=files)
    if 'automatic_recovery' in value:
        from . import native_recovery
        result['automatic_recovery'] = native_recovery.policy(value['automatic_recovery'])
    return result


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


def wake_allowed(limits):
    """Old full-resume grants retain wake; output-only needs its narrow opt-in."""
    return limits is not None and limits.get('execution_wake', not limits.get('output_only', False)) is True


def validate_wake_policy(root, step):
    limits = validate_launcher(root, step)
    if not wake_allowed(limits):
        raise ResumeUnavailable('worker_execution_wake_unauthorized')
    return limits



def validate_task(root, step):
    job = c.read(c.regular(root / 'jobs' / f'{step:03d}.json'))
    c._validate_inputs(root, c.workspace(root, step), job)
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
    from . import backends
    if not backends.selected(configuration_updates.for_step(root, step)).supports_resume:
        raise ResumeUnavailable('worker_resume_unsupported_backend')
    outcome_path = record / 'native-outcome.json'
    outcome = c.read(c.regular(outcome_path)) if outcome_path.exists() else {}
    thread = outcome.get('thread_id')
    if not outcome_path.exists():
        with c.regular(record / 'native.jsonl').open('rb') as stream:
            for line in stream:
                try:event = json.loads(line)
                except ValueError:continue  # The final line can be interrupted.
                if isinstance(event, dict) and event.get('type') == 'thread.started':
                    thread = event.get('thread_id')
                    break
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
                thread_event=event
                found = True
                break
    if not found:raise ResumeUnavailable('worker_resume_thread_unavailable')
    if home is None:
        number = execution.records(root, step).index(record)
        home = execution.home(root, step, number)
    home = Path(home)
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
            matches.append(dict(path=str(path), header=event))
    if len(matches) != 1:raise ResumeUnavailable('worker_resume_session_unavailable')
    return dict(thread_id=thread, home=str(home), workspace=str(c.workspace(root, step)),
                **(dict(thread_event=thread_event) if identity_proof else {}), **matches[0])


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
    from .protocol import require_current
    require_current(root)
    # Reentry continues an already accepted full task. The policy's output-only
    # restriction still governs new repairs/wakes, not this interrupted task.
    validate_launcher(root, step)
    job = validate_task(root, step)
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
            or original.get('execution_wake') and (settings.get('resume_policy') or {}).get('output_only')
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
    with (record / 'native-intent.json').open('x') as stream:
        json.dump(intent, stream);stream.flush();os.fsync(stream.fileno())
    runtime.start(root, step, number)
    return intent



def _work_exit(root,job,record,reason):
    from . import worker_delivery
    paths=execution.records(root,job['step'])
    _eligible_original(root,job['step'],paths,successful=True,wait_runner=True,
        budgeted=budget.node_limits(root,job) is not None)
    proof=worker_delivery.authorize_exit(root,job,record,reason)
    if not (record/'checkpoint.json').exists():checkpoint.capture(root,job,record)
    checkpoint.end_stage(root,job,record,reason,work_exit=proof)
    return c.seal_result(root,job['step'])


def finish_automatic_work(root, step, decision, reason):
    """Retain the exact completed contribution at a verified host boundary."""
    with budget._operator_writer(root):
        current=automatic_decision(root,step)
        if (current!=decision or current.get('work_repair') is None
                or current['action'] not in {'continue','stop'}):
            raise ResumeUnavailable('automatic_recovery_decision_stale')
        if reason not in {'worker_resume_node_exhausted','worker_resume_budget_unavailable',
                'automatic_recovery_continue_limit','wall_limit','token_limit','step_limit'}:
            raise ValueError('Automatic work exit requires a verified original host budget or continue cap')
        if reason=='automatic_recovery_continue_limit' and current['reason']!=reason:
            raise ValueError('Automatic work exit has no exhausted original continue cap')
        job=validate_task(root,step)
        return _work_exit(Path(root),job,execution.record(root,step),reason)


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
        decision=automatic_decision(root,step)
        if decision['action'] in {'continue','retry'}:raise AutomaticRecoveryRequired(decision)
        if decision['reason']=='automatic_recovery_continue_limit' and decision.get('work_repair') is not None:
            return _work_exit(root,job,record,decision['reason'])
        try:return start(root,step)
        except budget.NodeBudgetUnavailable as error:return _work_exit(root,job,record,error.reason)
        except ResumeUnavailable as error:
            return _work_exit(root,job,record,str(error))
    except OutputRepairRequired:
        if not (configuration_updates.for_step(root, step).get('resume_policy') or {}).get('output_repair', False):
            raise
        try:return start(root, step)
        except ResumeUnavailable as error:
            error.status='output_repair_unavailable'
            raise


def output_request(root, step, record, *, source_attempt=None, verify_task=True):
    """Recompute a repair binding from verified original final-response bytes."""
    job = validate_task(root, step) if verify_task else c.read(c.regular(root / 'jobs' / f'{step:03d}.json'))
    if source_attempt is None:source_attempt = len(execution.records(root, step)) - 1
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
        if c.file_ref(c.regular(raw, limit=None)) != outcome.get('raw_ref'):
            raise ValueError('Original final output changed before repair')
        try:
            science=c.science_payload(raw, job['role'], job.get('handoff_protocol', 1), 'selection' in job,**c.response_options(job))
            from . import candidate_refs
            science=candidate_refs.control_payload(job,science,root=root)
            from . import formal_review
            formal_review.validate_choice(root,job,science)
            from . import semantic
            interpretation,_=semantic.author_choices(root,job,science)
            if job['role']=='A' and not job.get('formal_review'):
                from . import search,candidate_nominations
                search.require_portfolio_review(root,job,science,interpretation=interpretation)
                candidate_nominations.require_final_coverage(root,job,science)
            if job['role']=='W':
                from . import worker_delivery, candidate_nominations
                worker_delivery.require_delivery(root,job,science,record,source_attempt=source_attempt)
                candidate_nominations.require(root,job,science)
            c.material_inventory(root, step)
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
    outcome=c.read(c.regular(prior/'native-outcome.json'))
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


def validate_repair(root, step, intent, *, prior_record=None, prior_intents=None, verify_task=True):
    request = intent.get('work_repair') or intent.get('output_repair')
    if request is None:
        return
    automatic_work = intent.get('continuation_kind') == 'automatic_recovery' and intent.get('work_repair') is not None
    if automatic_work:
        validate_automatic(root, step, intent, prior_record=prior_record, prior_intents=prior_intents)
        return
    if (configuration_updates.for_step(root, step).get('resume_policy') or {}).get('output_only') and intent.get('work_repair') is not None:
        raise ValueError('Output-only policy does not authorize scientific work repair')
    if intent.get('work_repair') is None and not validate_launcher(root, step).get('output_repair', False):
        raise ValueError('Output repair is not authorized by the frozen policy')
    prior = prior_record if prior_record is not None else execution.records(root, step)[intent['attempt'] - 1]
    expected=_accepted_repair_request(root,step,intent,prior)
    if expected is None:
        expected=output_request(root,step,prior,source_attempt=intent['attempt']-1,verify_task=verify_task)
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
    from . import fork_runtime
    for later in (number for number in fork_runtime.local_steps(root, include_native=True) if number > step):
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
                  task_ref=c.record_ref(root / 'jobs' / f'{step:03d}.json'),
                  root_intent_ref=c.record_ref(paths[0] / 'native-intent.json'), previous=execution.proof(last),
                  fragment_checkpoint=dict(path=(last / 'checkpoint.json').relative_to(root).as_posix()), budget_slice=anchor)
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
    if runtime.node_state(root, step) == 'interrupted':
        reenter(root, step)
        return native.wait_native(root, step, _reentry_started=True)
    if runtime.node_state(root, step) in {'running','waiting','unstarted'} or (len(paths) > 1 and runtime.node_state(root, step) == 'unknown'):
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
                if (last/'stage-end.json').exists():return c.seal_result(root, step)
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
    repairs = sum(c.read(path / 'native-intent.json').get('continuation_kind', 'repair') not in {'fragment','reentry','automatic_recovery'} for path in paths[1:])
    if repairs >= limits['max_attempts']:raise ResumeUnavailable('worker_resume_limit')
    settings = configuration_updates.for_step(root, step)
    from . import fork_runtime
    for later in (number for number in fork_runtime.local_steps(root, include_native=True) if number > step):
        if (execution.origin(root, later) / 'native-intent.json').exists():
            raise ResumeUnavailable('worker_resume_successor_present')
    original_intent = c.read(c.regular(paths[0] / 'native-intent.json'))
    if original_intent.get('task_ref') != c.record_ref(root / 'jobs' / f'{step:03d}.json'):
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
    intent = dict(step=step, attempt=number, continuation_kind='repair', **profile_binding, task_ref=c.record_ref(root / 'jobs' / f'{step:03d}.json'),
                  root_intent_ref=c.record_ref(paths[0] / 'native-intent.json'), previous=execution.proof(last),
                  session=session_ref, budget_slice=anchor,
                  **({('work_repair' if work_repair else 'output_repair'):repair} if repair is not None else {}))
    with (record / 'native-intent.json').open('x') as stream:
        json.dump(intent, stream);stream.flush();os.fsync(stream.fileno())
    runtime.start(root, step, number)
    return native.wait_native(root, step)


def validate_wake(root,step,intent,prior):
    from . import managed_wait, experiments
    job=c.read(root/'jobs'/f'{step:03d}.json')
    validate_wake_policy(root,step)
    if job['role']!='W':raise ValueError('Execution wake requires the original W task')
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
    limits=validate_wake_policy(root, step);job=validate_task(root,step);paths=execution.records(root,step);last=paths[-1]
    if (execution.origin(root,step)/'result-identity.json').exists():raise ResumeUnavailable('execution_wake_stage_already_sealed')
    if (root/'pause.request').exists() or budget.closeout_state(root)['begin'] is not None:
        raise ResumeUnavailable('worker_resume_paused_or_closeout')
    from . import correction
    if correction.gate(root,step-1) is not None:raise ResumeUnavailable('worker_resume_correction_paused')
    from . import fork_runtime
    if any((execution.origin(root,later)/'native-intent.json').exists()
            for later in fork_runtime.local_steps(root, include_native=True) if later > step):
        raise ResumeUnavailable('worker_resume_successor_present')
    attempts=sum(c.read(path/'native-intent.json').get('continuation_kind','repair') not in {'fragment','reentry','automatic_recovery'} for path in paths[1:])
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
        task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'),root_intent_ref=c.record_ref(paths[0]/'native-intent.json'),
        previous=execution.proof(last),session=session_ref,budget_slice=anchor,execution_wake=request)
    validate_wake(root,step,intent,last)
    with (record/'native-intent.json').open('x') as stream:
        json.dump(intent,stream);stream.flush();os.fsync(stream.fileno())
    runtime.start(root,step,number)
    return intent
