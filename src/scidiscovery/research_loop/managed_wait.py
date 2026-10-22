"""Control-owned waiting for the original managed execution, not a native retry."""
from pathlib import Path
import time

from scidiscovery.operations.execution import PENDING_EXECUTION_STATES, execution_status
from . import configuration_updates
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
        instruction=('Follow each executor response handoff.action; pending/running/queued/cancelling is not a terminal result. '
            'return_control means return your normal scientific JSON now with the original call and remaining limitations so the host can monitor the same call without a native turn. '
            'poll_original permits only that call, at handoff.poll_interval_seconds and at most handoff.max_queries queries total for this call in this turn; later replies do not reset that bound. '
            'Stop polling within the original budget; at the bound return the pending limitation normally. consume_terminal means interpret the original terminal result; reconcile_or_stop means preserve the original identity and reconcile once or report the unresolved obstacle, never blind polling or resubmission. '
            'If wake is authorized, verified original terminal facts may resume this same W session within its original attempts, permissions and budget; session availability is still checked at runtime. '
            'A narrow output-only wake interprets retained results and finalizes the response; it authorizes no new experiments or training. '
            'If wake is unavailable, report that limitation before choosing asynchronous work. Unknown submissions require same-call reconciliation, never resubmission.'))


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
        'Preserve the original call identity. Reconcile that same call if supported, or report the unresolved obstacle. Do not blindly poll, rerun or resubmit.')
    if not isinstance(config,dict):return stop('original_configuration_unavailable')
    call_id=result.get('call_id',call_id)
    if not isinstance(call_id,str):return stop('original_call_unavailable')
    if result.get('status') in {'unavailable','unresolved','integrity_error','caller_error','known_not_started','review_required'}:
        return stop('original_response_unavailable')
    try:
        records=e.Records(config);records.accepted(call_id)
        original=e.original_configuration(config,call_id)
        query=original['execution_interface']['capabilities']['query']
        state=_handoff_state(result.get('state',result.get('status')));verified=False
        if state=='unknown' and command not in {'collect','cancel'}:
            return stop('original_state_unknown')
        observation=records.directory(call_id)/'execution-observation.json'
        if observation.exists():
            observed=c.read(c.regular(observation))
            e.verify_observation(observed,lambda proof:Path(proof['path']),interface=original['execution_interface'])
            state=observed['status'];verified=True
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
                    and intent.get('task_sha256')==c.digest(root/'jobs'/f'{step:03d}.json')):
                wake=True
        action=handoff_action(state,original_bound=external is not None,query=query,
            wake_authorized=wake,terminal_verified=verified)
        if action=='consume_terminal':
            return hint(action,'original_terminal_verified',
                'Interpret this verified original terminal result and its retained outputs. Preserve scientific limitations; do not rerun or resubmit it.')
        if action=='return_control':
            return hint(action,'host_wait_authorized',
                'Return your normal scientific JSON now with this pending original call and its limitations. The host will preserve the unsealed checkpoint and monitor this same call without another native turn; terminal wake still requires the original session and budget.')
        if action=='reconcile_or_stop':return stop('original_terminal_unverified' if state in {'known_success','known_failed'} else 'original_state_or_query_unavailable')
        return hint(action,'host_wake_unavailable',
            'Query only this original call at the stated interval, at most max_queries total for this call in this turn; later replies do not reset the bound. Stay within the original budget. At the bound return normal scientific JSON retaining the pending limitation; never resubmit.',
            poll_interval_seconds=POLL_SECONDS,max_queries=3)
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
    if (value['job_sha256']!=c.digest(root/'jobs'/f'{job["step"]:03d}.json')
            or value['checkpoint_sha256']!=c.digest(c.regular(record/'checkpoint.json'))
            or value['outcome_sha256']!=c.digest(c.regular(record/'native-outcome.json'))):
        raise e.IntegrityError('Execution waiting checkpoint changed')
    checkpoint.validate(root,job,record)
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


def before_seal(root,job,record):
    """Only original bound execution facts may suspend a successful W turn."""
    if job['role']!='W':return
    outcome=c.read(c.regular(record/'native-outcome.json'))
    raw=record/'science-output.json'
    if outcome.get('output_status')=='missing' or not raw.is_file() or raw.is_symlink():return
    if (type(outcome.get('returncode')) is not int or outcome.get('returncode')!=0 or outcome.get('stop_reason')
            or outcome.get('usage',{}).get('unknown_steps') and not budget.native_completion_known(outcome, configuration_updates.for_job(root, job))
            or outcome.get('raw_sha256')!=c.digest(c.regular(record/'science-output.json'))):return
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
    checkpoint.capture(root,job,record)
    value=dict(job_sha256=c.digest(root/'jobs'/f'{job["step"]:03d}.json'),
        checkpoint_sha256=c.digest(record/'checkpoint.json'),outcome_sha256=c.digest(record/'native-outcome.json'),calls=calls)
    c.write(record/NAME,value);(record/NAME).chmod(0o400)
    raise ExecutionWaiting()


def pending(root):
    """Read-only clock classification; no backend query and no new authority."""
    from . import execution_recovery
    if execution_recovery.pending(root):return True
    for path in (Path(root)/'native-evidence').glob('*/**/'+NAME):
        record=path.parent
        step=int(record.relative_to(Path(root)/"native-evidence").parts[0])
        job=c.read(Path(root)/'jobs'/f'{step:03d}.json')
        if execution.record(root,job['step'])!=record:continue
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
    while True:
        terminals=[];has_pending=False
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
        if (root/'pause.request').exists():raise ExecutionWaiting('paused_before_execution_wake')
        if budget.snapshot(root)['seconds_remaining']<=0:raise ExecutionWaiting('managed_execution_wait_deadline')
        if not has_pending:
            continuation.wake_start(root,step,terminals)
            return
        budget.transition(root,'waiting','managed_execution_pending')
        time.sleep(min(POLL_SECONDS,budget.snapshot(root)['seconds_remaining']))


def wake_packet(root,job,intent):
    """Present verified scientific results to the original W, with bounded navigation."""
    rows=[];work=c.workspace(root,job['step'])
    for number,proof in enumerate(intent['execution_wake']['observations'],1):
        observed=c.read(Path(proof['path']));directory=Path(proof['path']).parent
        completed=c.read(Path(observed['completed']['path']))
        prefix=f'work/execution-results/{number:03d}'
        if type(job.get('worker_view_version')) is int and job['worker_view_version'] == 1:
            if e.proof(proof['path']) != proof:
                raise e.IntegrityError('Execution wake observation original changed')
            accepted=c.read(c.regular(Path(observed['accepted']['path'])))
            _,config=_config(root,job,accepted)
            e.verify_observation(observed,lambda item:Path(item['path']),interface=config['execution_interface'])
            result={key:completed[key] for key in ('status','native_state','returncode','wall_seconds',
                'evaluation_key','metric','val_score','val_acc') if key in completed}
            if isinstance(completed.get('result'),dict):result['result']=e._scientific_result(completed['result'])
            c.write(work/prefix/'result.json',result)
            outputs=[]
            for item in e.scientific_outputs(config,observed):
                target=prefix+'/outputs/'+item.name
                c._copy_bound(Path(item.local_path),work/target,item.sha256)
                outputs.append(dict(name=item.name,path=target))
            rows.append(dict(status=observed['status'],result=prefix+'/result.json',outputs=outputs,
                **(dict(native_state=observed['native_state']) if 'native_state' in observed else {})))
            continue
        c._copy_bound(Path(observed['completed']['path']),work/prefix/'result.json',observed['completed']['sha256'])
        outputs=[]
        for item in observed['outputs']:
            target=prefix+'/outputs/'+item['name']
            c._copy_bound(Path(item['path']),work/target,item['sha256']);outputs.append(dict(name=item['name'],path=target))
        originals=[]
        for index,item in enumerate(observed['proofs'],1):
            target=prefix+f'/originals/{index:03d}'
            c._copy_bound(Path(item['path']),work/target,item['sha256']);originals.append(target)
        rows.append(dict(status=observed['status'],original=prefix+'/result.json',outputs=outputs,native_originals=originals,
            **(dict(native_state=observed['native_state']) if 'native_state' in observed else {})))
    c.write(work/'work/execution-results.json',dict(executions=rows,
        limitation='Original execution facts only; interpret their scientific meaning and limitations. Do not resubmit these executions.'))
