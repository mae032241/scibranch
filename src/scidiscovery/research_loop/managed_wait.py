"""Control-owned waiting for the original managed execution, not a native retry."""
from pathlib import Path
import time

from scidiscovery.operations.execution import PENDING_EXECUTION_STATES, execution_status
from . import budget_limits, configuration_updates
from . import budget, contracts as c, execution, experiments as e, checkpoint, experiment_client

POLL_SECONDS = 1.
NAME = 'execution-wait.json'


class ExecutionWaiting(Exception):
    def __init__(self, reason='managed_execution_pending'):
        self.reason=reason
        super().__init__(reason)


def capability(root,job):
    """Describe frozen wake permission without claiming a usable native session."""
    from . import continuation
    limits=c.settings(Path(root),job).get('resume_policy')
    allowed=continuation.wake_allowed(limits)
    return dict(wake_authorized=allowed,
        unavailable_reason=None if allowed else ('worker_resume_unsupported' if limits is None else 'worker_execution_wake_unauthorized'),
        instruction=('Follow each executor response handoff.action and instruction; pending is not terminal. '
            'Wake requires frozen permission, the original W task and session, and cumulative shared budget, checked at runtime. '
            'After consuming verified terminal results, continue that task; distinct follow-up experiments require normal admission. '
            'Output repair remains a separate JSON-only turn and cannot start scientific execution. '
            'If wake is unavailable, report that limitation before choosing asynchronous work. '
            'Unknown submissions require same-call reconciliation, never resubmission.'))


def _handoff_state(state):
    """Accept native states and canonical facts without demoting verified facts."""
    return state if isinstance(state,str) and state in {'known_success','known_failed'} else execution_status(state)


def handoff_action(state,*,original_bound,query,wake_authorized,terminal_verified=False):
    """One rule for executor hints and host monitoring; strings grant nothing."""
    state=_handoff_state(state)
    if not original_bound:return 'reconcile_or_stop'
    if state in {'known_success','known_failed'}:
        return 'consume_terminal' if terminal_verified else 'reconcile_or_stop'
    if state in PENDING_EXECUTION_STATES and query:
        return 'return_control' if wake_authorized else 'poll_original'
    return 'reconcile_or_stop'


def handoff(config,result,command,call_id=None):
    """A replaceable next-step hint, derived from host originals and returned state."""
    def hint(action,reason,instruction,**fields):
        return dict(action=action,reason=reason,instruction=instruction,
            **(dict(call_id=call_id) if call_id is not None else {}),**fields)
    stop=lambda reason:hint('reconcile_or_stop',reason,
        'Reconcile this same original call once if supported, or report the unresolved obstacle. Never blindly poll, rerun or resubmit.')
    if result.get('accepted') is False and result.get('status')=='caller_error':
        return dict(action='correct_request',reason='execution_not_accepted',
            instruction=('This request was not accepted; there is no original call from this request to look up. '
                'Correct the reported arguments or inputs, then submit the corrected request. '
                'Preserve any separately accepted original call and its unresolved status; never resubmit it.'))
    if (result.get('accepted') is False and result.get('status')=='review_required'
            and result.get('reason') in {'worker_progress_review_due',
                'worker_context_review_due','worker_context_observation_unknown'}):
        return hint('finalize_response',result['reason'],
            'This new execution was not accepted. Independent A assessment is due for this W stage. '
            'Consume verified results from separately accepted original calls; reconcile or wait for those same calls as required. '
            'Return normal scientific JSON with stage_intent=review, retaining findings, uncertainty and any unresolved original-call limitations. '
            'Do not submit another batch, repair this request, rerun or resubmit accepted calls.')
    if (result.get('accepted') is False
            and (result.get('status'),result.get('reason')) in {
                ('budget_exhausted','shared_time_exhausted'),
                ('budget_exhausted','shared_budget_exhausted'),
                ('budget_unavailable','shared_usage_unknown')}):
        return dict(action='finalize_response',reason=result['reason'],
            instruction=('Return normal scientific JSON now. Stop new execution requests, preparation, review requests and polling. '
                'Preserve existing code, design and evidence; report that this requested execution was not performed and state the unmet goal and limitations. '
                'Preserve any separately accepted original call and its unresolved status; never resubmit it. '
                + ('The host applies the original budget and FINAL policy.' if result['status']=='budget_exhausted' else
                   'Shared usage remains unknown; do not report zero usage, exhausted balance, successful completion or an automatic FINAL decision.')))
    if result.get('accepted') is False and result.get('status') in {'known_not_started','review_required','unresolved'}:
        return dict(action='reconcile_or_stop',reason='execution_not_accepted',
            instruction='This request was not accepted. Resolve the reported admission, input or local cleanup issue before another request; preserve any separately reported original call.')
    if not isinstance(config,dict):return stop('original_configuration_unavailable')
    call_id=result.get('call_id',call_id)
    if not isinstance(call_id,str):return stop('original_call_unavailable')
    refused=(result.get('accepted') is True and result.get('status') in {'unavailable','review_required'})
    if result.get('status') in {'unavailable','unresolved','integrity_error','caller_error','known_not_started','review_required'} and not refused:
        return stop('original_response_unavailable')
    try:
        records=e.Records(config);records.accepted(call_id)
        original=e.original_configuration(config,call_id)
        query=original['execution_interface']['capabilities']['query']
        state=_handoff_state(result.get('state',result.get('status')));verified=False
        observed=e._retained_observation(config,call_id)
        if observed is not None:
            state=observed['status'];verified=True
        elif refused:
            return hint('reconcile_or_stop','original_operation_unavailable',
                'The requested operation was not performed and no verified retained terminal result is available. Report the local preflight, interface loading, capability or permission obstacle for this same accepted call; never rerun or resubmit it.')
        elif state=='unknown' and command not in {'collect','cancel'}:
            return stop('original_state_unknown')
        elif command=='cancel':
            # Cancellation acknowledgement is not the original execution state.
            state='unknown'
        external=records.external(call_id)
        from . import continuation
        root=experiment_client.root_for_configuration(config,allow_standalone=True)
        wake=False
        if root is not None and external is not None:
            step=original['scope']['step'];job=c.read(c.regular(root/'jobs'/f'{step:03d}.json'))
            limits=c.settings(root,job).get('resume_policy')
            current=execution.record(root,step)
            intent=c.read(c.regular(current/'native-intent.json'))
            if (job['role']=='W' and continuation.wake_allowed(limits)
                    and original.get('native_attempt')==dict(number=len(execution.records(root,step))-1,
                        execution_key=execution.identity(root,step))
                    and intent.get('task_ref')==c.record_ref(root/'jobs'/f'{step:03d}.json')):
                wake=True
        action=handoff_action(state,original_bound=external is not None,query=query,
            wake_authorized=wake,terminal_verified=verified)
        if action=='consume_terminal':
            failure=observed.get('public_result',{}).get('author_file_error')
            if failure is not None:
                log=f'work/retained-experiments/{call_id}/outputs/'+failure['log_name']
                return hint(action,'author_file_error',
                    failure['diagnostic']+'. Read '+log+'. Decide whether author-file closeout needs correction. '
                    'Reuse retained results; do not retrain, rerun completed experiments or copy retained originals again. '
                    'Only original permissions and budget apply. Preserve the failed call and scientific limitations. '
                    'You own candidate suitability: rejecting every candidate is valid. If no further correction is warranted or available, set stage_intent=decision and state any blocker.',
                    author_file_error={key:failure[key] for key in ('error','errno','diagnostic')}|dict(log=log))
            return hint(action,'original_terminal_verified',
                'Read this verified original terminal result and retained outputs; interpret their scientific meaning and limitations. Do not resubmit.')
        if action=='return_control':
            return hint(action,'host_wait_authorized',
                'Return normal scientific JSON now with this pending original call and limitations. The host monitors this same call; wake still requires the original session and budget.')
        if action=='reconcile_or_stop':return stop('original_terminal_unverified' if state in {'known_success','known_failed'} else 'original_state_or_query_unavailable')
        return hint(action,'host_wake_unavailable',
            'Query only this same original call in this native turn at poll_interval_seconds while pending and queryable, until handoff changes or an original finite external-call deadline, resource boundary or host stop is reached. Shared soft targets do not terminate this accepted node; reported usage is a lower bound and unknown coverage is not zero usage. Shared ceilings are checked before the next node or wake. No query-count limit. At an actual stop, return normal scientific JSON with the pending limitation.',
            poll_interval_seconds=POLL_SECONDS)
    except (OSError,ValueError,KeyError,TypeError):
        return stop('original_handoff_unverified')


def _config(root,job,accepted):
    number=accepted['native_attempt']['number']
    path=execution.record(root,job['step'],number)/'experiment-config.json'
    config=c.read(c.regular(path))
    records=e.Records(config)
    if (config['execution_interface']!=accepted['interface'] or config['scope']!=accepted['scope']
            or records.accepted(accepted['call_id'])!=accepted):
        raise e.IntegrityError('Waiting execution differs from its frozen host owner')
    return path,config


def validate(root,job,record):
    value=c.read(c.regular(record/NAME,limit=None))
    if (value['job_ref']!=c.record_ref(root/'jobs'/f'{job["step"]:03d}.json')
            or value['checkpoint_ref']!=c.record_ref(c.regular(record/'checkpoint.json'))
            or value['outcome_ref']!=c.record_ref(c.regular(record/'native-outcome.json'))):
        raise e.IntegrityError('Execution waiting checkpoint changed')
    if not isinstance(value['calls'],list) or not value['calls']:
        raise e.IntegrityError('Execution waiting requires original calls')
    seen=set()
    for item in value['calls']:
        for key in ('accepted','submission','config'):
            if e.proof(item[key]['path'])!=item[key]:raise e.IntegrityError('Waiting execution original changed')
        accepted=c.read(Path(item['accepted']['path']))
        path,config=_config(root,job,accepted)
        if (str(path)!=item['config']['path'] or accepted['call_id'] in seen
                or e.Records(config).external(accepted['call_id'])!=item['external_run_id']):
            raise e.IntegrityError('Waiting execution identity changed')
        seen.add(accepted['call_id'])
    return value


def before_seal(root,job,record,*,inventory):
    """Only original bound execution facts may suspend a successful W turn."""
    if job['role']!='W':return
    outcome=c.native_outcome(record)
    raw=record/'science-output.json'
    if outcome.get('output_status')=='missing' or not raw.is_file() or raw.is_symlink():return
    if (type(outcome.get('returncode')) is not int or outcome.get('returncode')!=0 or outcome.get('stop_reason')
            or outcome.get('usage',{}).get('unknown_steps') and not budget.native_completion_known(outcome, configuration_updates.for_job(root, job))
            or outcome.get('raw_ref')!=c.file_ref(c.regular(record/'science-output.json'))):return
    if (record/NAME).exists():
        validate(root,job,record)
        raise ExecutionWaiting()
    root=Path(root)
    records=e.Records(dict(record_dir=str(experiment_client.record_directory(root,job['step']))))
    calls=[]
    for directory in records.calls():
        accepted=c.read(c.regular(directory/'accepted.json'))
        path,config=_config(root,job,accepted)
        external=e.Records(config).external(directory.name)
        if external is None:continue
        observed=directory/'execution-observation.json'
        if observed.exists():
            e.verify_observation(c.read(observed),lambda item:Path(item['path']),interface=config['execution_interface'])
            continue
        if not config['execution_interface']['capabilities']['query']:continue
        adapter=e.load(config)
        details=adapter.status_details(directory.name)
        state=execution_status(details.get('state'))
        if state not in PENDING_EXECUTION_STATES and state not in {'known_success','known_failed'}:continue
        # A terminal first seen after this native turn also needs W consumption.
        if state not in PENDING_EXECUTION_STATES and e.record_observation(config,adapter,directory.name) is None:continue
        calls.append(dict(accepted=e.proof(directory/'accepted.json'),submission=e.proof(directory/'submission.json'),
            config=e.proof(path),external_run_id=external,observed_state=details['state']))
    if not calls:return
    from . import worker_delivery
    worker_delivery.writers_stopped(root,job,record)
    if inventory.checkpoint is None:raise e.IntegrityError('Owner checkpoint is unavailable')
    value=dict(job_ref=c.record_ref(root/'jobs'/f'{job["step"]:03d}.json'),
        checkpoint_ref=c.record_ref(record/'checkpoint.json'),outcome_ref=c.record_ref(record/'native-outcome.json'),calls=calls)
    c.write(record/NAME,value);(record/NAME).chmod(0o400)
    raise ExecutionWaiting()


def pending(root):
    """Read-only clock classification; no backend query and no new authority."""
    from . import execution_recovery,accepted_budget
    if execution_recovery.pending(root):return True
    current=accepted_budget.read(root).current_job
    if current is None or current['status']=='sealed' or current['native_record'] is None:return False
    job=c.read(Path(root)/'jobs'/f'{current["step"]:03d}.json')
    record=Path(root)/current['native_record']
    if not (record/NAME).exists():return False
    value=validate(root,job,record)
    for item in value['calls']:
        path=Path(item['accepted']['path']).parent/'execution-observation.json'
        if not path.exists():return True
        accepted=c.read(Path(item['accepted']['path']));_,config=_config(root,job,accepted)
        e.verify_observation(c.read(path),lambda proof:Path(proof['path']),interface=config['execution_interface'])
    return False


def wait_and_wake(root,step):
    """The owning run process monitors without native turns; restart uses the same facts."""
    root=Path(root);job=c.read(root/'jobs'/f'{step:03d}.json');record=execution.record(root,step)
    value=validate(root,job,record)
    # Retain the original checkpoint, but diagnose a frozen missing capability
    # before spending the remaining wall budget monitoring an unwakeable task.
    from . import continuation
    continuation.validate_wake_policy(root,step)
    from . import execution_wait_monitor
    while True:
        terminals=[];has_pending=False;states={}
        for item in value['calls']:
            accepted=c.read(Path(item['accepted']['path']));_,config=_config(root,job,accepted)
            path=Path(item['accepted']['path']).parent/'execution-observation.json'
            if path.exists():
                e.verify_observation(c.read(path),lambda proof:Path(proof['path']),interface=config['execution_interface'])
            else:
                adapter=e.load(config)
                if not config['execution_interface']['capabilities']['query']:
                    raise ExecutionWaiting('managed_execution_query_unavailable')
                state=execution_status(adapter.status_details(accepted['call_id']).get('state'))
                states[Path(item['accepted']['path']).parent.name]=state
                action=handoff_action(state,original_bound=True,query=True,wake_authorized=True)
                if action=='return_control':has_pending=True;continue
                if state not in {'known_success','known_failed'}:raise ExecutionWaiting('managed_execution_status_unknown')
                if e.record_observation(config,adapter,accepted['call_id']) is None:
                    raise ExecutionWaiting('managed_execution_terminal_unverified')
            observed=c.read(path)
            if handoff_action(observed['status'],original_bound=True,query=config['execution_interface']['capabilities']['query'],
                    wake_authorized=True,terminal_verified=True)!='consume_terminal':
                raise ExecutionWaiting('managed_execution_terminal_unverified')
            terminals.append(e.proof(path))
            states[Path(item['accepted']['path']).parent.name]=observed['status']
        execution_wait_monitor.record_poll(root,step,states)
        if (root/'pause.request').exists():raise ExecutionWaiting('paused_before_execution_wake')
        remaining=budget.snapshot(root)['seconds_remaining']
        if budget_limits.reached(remaining):
            raise ExecutionWaiting('managed_execution_wait_deadline' if has_pending else 'managed_execution_terminal_wake_deadline')
        if not has_pending:
            continuation.wake_start(root,step,terminals)
            return
        budget.transition(root,'waiting','managed_execution_pending')
        remaining=budget.snapshot(root)['seconds_remaining']
        time.sleep(POLL_SECONDS if remaining is None else min(POLL_SECONDS,max(0.,remaining)))


def wake_packet(root,job,intent):
    """Present verified scientific results to the original W, with bounded navigation."""
    rows=[];work=c.workspace(root,job['step'])
    from . import shared_input_views
    shared_input_views.dynamic_root(root,work,'execution-results')
    from . import storage_policy as storage
    frozen=c.settings(root,job)
    context=storage.Deadline(65 if frozen['max_seconds'] is None else frozen['max_seconds'])
    inventory=storage.Inventory(frozen.get('storage_policy'),context=context)
    for proof in intent['execution_wake']['observations']:
        if e.proof(proof['path'])!=proof:raise e.IntegrityError('Execution wake observation original changed')
        observed=c.read(Path(proof['path']));directory=Path(proof['path']).parent
        completed=c.read(Path(observed['completed']['path']))
        shared_input_views.dynamic_root(root,work,'execution-results')
        accepted=c.read(c.regular(Path(observed['accepted']['path'])))
        prefix='work/execution-results/'+accepted['call_id']
        if type(job.get('worker_view_version')) is int and job['worker_view_version'] == 1:
            if e.proof(proof['path']) != proof:
                raise e.IntegrityError('Execution wake observation original changed')
            _,config=_config(root,job,accepted)
            e.verify_observation(observed,lambda item:Path(item['path']),interface=config['execution_interface'])
            result=e.scientific_result(config,accepted,completed,observed)
            destination=work/prefix/'result.json'
            if destination.exists():
                if c.read(c.regular(destination))!=result:raise e.IntegrityError('Execution wake public result changed')
            else:c.write(destination,result)
            outputs=[]
            for item in e.scientific_outputs(config,observed):
                inventory.entry();inventory.file(item.size_bytes)
                target=prefix+'/outputs/'+item.name
                outputs.append(dict(name=item.name,path=target))
            rows.append(dict(status=observed['status'],result=prefix+'/result.json',outputs=outputs,
                **(dict(native_state=observed['native_state']) if 'native_state' in observed else {})))
            continue
        _,config=_config(root,job,accepted)
        e.verify_observation(observed,lambda item:Path(item['path']),interface=config['execution_interface'])
        outputs=[]
        for item in observed['outputs']:
            target=prefix+'/outputs/'+item['name']
            inventory.entry();inventory.file(item['size_bytes'])
            outputs.append(dict(name=item['name'],path=target))
        originals=[]
        for index,item in enumerate(observed['proofs'],1):
            target=prefix+f'/originals/{index:03d}'
            inventory.entry();inventory.file(item['size_bytes'])
            originals.append(target)
        rows.append(dict(status=observed['status'],original=prefix+'/result.json',outputs=outputs,native_originals=originals,
            **(dict(native_state=observed['native_state']) if 'native_state' in observed else {})))
    c.write(work/'work/execution-results.json',dict(executions=rows,
        limitation='Original execution facts only; interpret their scientific meaning and limitations. Do not resubmit these executions.'))
