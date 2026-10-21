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
                if state in PENDING_EXECUTION_STATES:has_pending=True;continue
                if state not in {'known_success','known_failed'}:raise ExecutionWaiting('managed_execution_status_unknown')
                if e.record_observation(config,adapter,accepted['call_id']) is None:
                    raise ExecutionWaiting('managed_execution_terminal_unverified')
            terminals.append(e.proof(path))
        if (root/'pause.request').exists():raise ExecutionWaiting('paused_before_execution_wake')
        if budget.snapshot(root)['seconds_remaining']<=0:raise ExecutionWaiting('managed_execution_wait_deadline')
        if not has_pending:
            from . import continuation
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
