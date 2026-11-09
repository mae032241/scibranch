"""Durable receipts for detached control and boundary budget amendments."""
from pathlib import Path
import fcntl
import os
import subprocess
import sys
import time
import uuid

from . import budget, contracts, controller, runtime


def _path(root, operation_id):
    if not isinstance(operation_id, str) or len(operation_id) != 32 or any(c not in '0123456789abcdef' for c in operation_id):
        raise ValueError('Exact operation ID required')
    return Path(root) / 'host-control' / 'operations' / f'{operation_id}.json'


def _new(kind, **fields):
    return dict(operation_id=uuid.uuid4().hex, kind=kind, status='accepted', **fields)


def _read(root, operation_id):
    return contracts.read(contracts.regular(_path(root, operation_id)))


def _replay(root, operation_id, kind, request):
    """Read one exact existing intent; never restart its accepted work."""
    path = _path(root, operation_id)
    if not path.exists() and not path.is_symlink():
        return None
    value = _read(root, operation_id)
    if (value.get('operation_id') != operation_id or value.get('kind') != kind
            or value.get('request') != request):
        raise ValueError('Operation ID belongs to another exact control request')
    return value


def operation(root, operation_id):
    root = Path(root).resolve()
    from .instance_owner import lease
    with lease(root):
        descriptor = os.open(root / 'host-run.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            # Probe before the ledger: the starter/child owns writer -> ledger.
            # A busy writer may still be publishing its child's exact identity.
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                inactive = True
            except BlockingIOError:
                inactive = False
            with budget._ledger_writer(root):
                value = _read(root, operation_id)
                process = value.get('process')
                if (value['kind'] == 'controller' and value['status'] in {'accepted', 'running'}
                        and ((process is None and inactive)
                             or (process is not None and not runtime.process_matches(process, operation_id)))):
                    value.update(status='failed', error='Controller owner disappeared; original work requires reconciliation; missing process does not prove non-submission')
                    contracts.write(_path(root, operation_id), value)
                return value
        finally:
            os.close(descriptor)


def operations(root):
    root = Path(root).resolve()
    return sorted([operation(root, path.stem) for path in sorted((root / 'host-control' / 'operations').glob('*.json'))], key=lambda row: row.get('sequence', 0))


def stop_held_recovery(root, *, held_operation_id, active_operation_id):
    """Explicit cleanup of a held repair and its exact accepted current owner.

    This mutator is separate from status/operation readers. It neither launches
    nor reconciles science, and returns uncertainty instead of signalling an
    unbound process or refunding any accepted consumption.
    """
    from .instance_owner import lease
    root=Path(root).resolve()
    unknown=dict(status='cleanup_unknown',cleanup_confirmed=False,
        held_operation_id=held_operation_id,active_operation_id=active_operation_id)
    with lease(root):
        held=_read(root,held_operation_id);active=_read(root,active_operation_id)
        action=held.get('action') or {}
        step=action.get('step');source=action.get('expected_attempt',0)
        if (held.get('operation_id') != held_operation_id or held.get('kind') != 'controller'
                or action.get('kind') != 'worker_resume' or type(step) is not int or step <= 0
                or type(source) is not int or source < 0
                or action.get('job_ref') != contracts.record_ref(root/'jobs'/f'{step:03d}.json')
                or active.get('operation_id') != active_operation_id or active.get('kind') != 'controller'
                or (active.get('action') or {}).get('kind') not in {'run','continue','worker_resume'}
                or type(held.get('sequence')) is not int or type(active.get('sequence')) is not int
                or active['sequence'] < held['sequence']):
            return unknown
        process=active.get('process')
        if not isinstance(process,dict) or process.get('task_ref') != active_operation_id:return unknown
        from .instance_control import request_pause
        request_pause(root)  # Existing next-node barrier precedes forced cleanup.
        if runtime.stop_bound_group(process,active_operation_id) != 'observed_group_cleaned':return unknown
        descriptor=os.open(root/'host-run.lock',os.O_CREAT|os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
        try:
            try:
                fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                return unknown  # A later owner must never be cleaned as this repair.
            identity=os.fstat(descriptor)
            current=os.stat(root/'host-run.lock',follow_symlinks=False)
            if (identity.st_dev,identity.st_ino)!=(current.st_dev,current.st_ino):return unknown
            return _stop_held_native_owner(root,unknown)
        finally:
            os.close(descriptor)


def _stop_held_native_owner(root, unknown):
    """The explicit cleanup writer holds host-run.lock through this boundary."""
    from . import accepted_budget, execution, native_recovery
    head=accepted_budget.read(root)
    job=head.current_job
    if job is None or job.get('native_record') is None:
        return dict(unknown,status='held_recovery_stopped',cleanup_confirmed=True)
    number=job.get('step');relative=job.get('native_record')
    if type(number) is not int or number <= 0 or not isinstance(relative,str):return unknown
    record=root/relative
    if record.resolve()!=record or not record.is_relative_to(root):return unknown
    accepted=accepted_budget.native_attempt(root,number,record,head=head)
    if accepted is None:return unknown
    intent=accepted['intent'];attempt=intent.get('attempt',0)
    origin=execution.origin(root,number)
    expected=origin if attempt==0 else origin/'continuations'/f'{attempt:03d}' if type(attempt) is int else None
    if (type(attempt) is not int or attempt < 0 or record != expected
            or intent.get('task_ref') != job['job_ref']
            or contracts.read(contracts.regular(record/'native-intent.json')) != intent):return unknown
    binding=job['job_ref'] if attempt==0 else contracts.record_ref(record/'native-intent.json')
    receipts={}
    for name in ('native-process.json','runner-process.json','watchdog-process.json'):
        path=record/name
        if path.exists() or path.is_symlink():
            receipt=contracts.read(contracts.regular(path))
            if not isinstance(receipt,dict) or receipt.get('task_ref') != binding:return unknown
            receipts[name]=receipt
    if not {'runner-process.json','watchdog-process.json'} <= set(receipts):return unknown
    if 'native-process.json' not in receipts:
        outcome=record/'native-outcome.json'
        if not outcome.exists() or not native_recovery.never_started(contracts.read(contracts.regular(outcome))):
            return unknown
    cleanup=True
    for name in ('native-process.json','runner-process.json','watchdog-process.json'):
        receipt=receipts.get(name)
        if receipt is None:continue
        if name=='runner-process.json' and 'native-process.json' in receipts:
            until=time.monotonic()+3  # Original runner/watchdog cleanup grace.
            while runtime.process_matches(receipt,binding) and time.monotonic()<until:
                time.sleep(.02)
        cleanup=runtime.stop_bound_group(receipt,binding)=='observed_group_cleaned' and cleanup
    return dict(unknown,status='held_recovery_stopped' if cleanup else 'cleanup_unknown',
        cleanup_confirmed=cleanup,step=number,attempt=attempt)


def confirm_no_held_recovery(root, *, goal_id, step, expected_attempt):
    """Fence a cancelled hold and positively prove no replacement was accepted.

    An empty UI result is not evidence. The existing pause barrier and writer
    fence keep a later admission out while this owner reads original records.
    """
    from .instance_owner import lease
    root=Path(root).resolve()
    unknown=dict(status='cleanup_unknown',cleanup_confirmed=False,no_replacement=False,
        step=step,expected_attempt=expected_attempt)
    with lease(root):
        if (not isinstance(goal_id,str) or contracts.settings(root).get('goal_id') != goal_id
                or type(step) is not int or step <= 0
                or type(expected_attempt) is not int or expected_attempt < 0):return unknown
        descriptor=None
        try:
            descriptor=os.open(root/'host-run.lock',os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW)
            fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
            identity=os.fstat(descriptor)
            current=os.stat(root/'host-run.lock',follow_symlinks=False)
            if (identity.st_dev,identity.st_ino)!=(current.st_dev,current.st_ino):return unknown
            from .instance_control import request_pause
            request_pause(root)
            return _confirm_no_replacement_originals(root,goal_id,step,expected_attempt,unknown)
        except (OSError,ValueError,KeyError,TypeError):
            return unknown
        finally:
            if descriptor is not None:os.close(descriptor)


def _confirm_no_replacement_originals(root, goal_id, step, attempt, unknown):
    """Produce exact original facts only while the cleanup writer is fenced."""
    from . import accepted_budget, continuation, execution
    task_ref=contracts.record_ref(root/'jobs'/f'{step:03d}.json')
    directory=root/'host-control/operations'
    if directory.resolve()!=directory or directory.exists() and not directory.is_dir():return unknown
    for path in directory.glob('*.json'):
        operation=_read(root,path.stem)
        if not isinstance(operation,dict) or operation.get('operation_id') != path.stem:return unknown
        action=operation.get('action') or {}
        if (operation.get('kind') == 'controller' and action.get('kind') == 'worker_resume'
                and action.get('step') == step and action.get('expected_attempt',0) == attempt
                and action.get('job_ref') == task_ref):return unknown
        if operation.get('kind') == 'controller':
            if operation.get('status') in {'accepted','running'}:return unknown
            if not runtime.bound_group_stopped(operation.get('process'),operation.get('operation_id')):
                return unknown
    head=accepted_budget.read(root)
    job=head.current_job
    record=execution.origin(root,step)
    if attempt:record=record/'continuations'/f'{attempt:03d}'
    relative=record.relative_to(root).as_posix()
    if (head['instance_id'] != goal_id or job is None or job.get('step') != step
            or job.get('job_ref') != task_ref or job.get('native_record') != relative
            or head['current_native'] is not None and (
                head['current_native'].get('record') != relative or head['current_native'].get('step') != step)):
        return unknown
    accepted=accepted_budget.native_attempt(root,step,record,head=head)
    if (accepted is None or accepted['intent'].get('attempt',0) != attempt
            or accepted['intent'].get('task_ref') != task_ref
            or contracts.read(contracts.regular(record/'native-intent.json')) != accepted['intent']):return unknown
    outcome=contracts.read(contracts.regular(record/'native-outcome.json'))
    facts=continuation._prelaunch_original_facts(root,step,record,attempt=attempt)
    continuation._require_never_started(outcome,facts)
    if not runtime.bound_group_stopped(facts['receipt'],facts['binding']):return unknown
    watchdog=record/'watchdog-process.json'
    if watchdog.exists() or watchdog.is_symlink():
        if not runtime.bound_group_stopped(contracts.read(contracts.regular(watchdog)),facts['binding']):
            return unknown
    return dict(unknown,status='held_recovery_stopped',cleanup_confirmed=True,no_replacement=True)


def start_background(root, *, continue_run=False, ack_correction=None, authorization_note=None, resume_step=None,
                     operation_id=None,expected_attempt=None,after_interrupt=False,native_intent_ref=None,new_context=False):
    """Hand the existing single-writer lock to an independent controller process."""
    root = Path(root).resolve()
    from .instance_owner import lease
    from .protocol import require_current
    with lease(root):
        require_current(root)
        request = dict(continue_run=bool(continue_run), ack_correction=ack_correction,
                       authorization_note=authorization_note, resume_step=resume_step)
        if expected_attempt is not None:
            if resume_step is None or type(expected_attempt) is not int or expected_attempt < 0:
                raise ValueError('Expected attempt requires an exact worker resume source')
            request['expected_attempt']=expected_attempt
        if after_interrupt:
            if resume_step is None or expected_attempt is None or not isinstance(native_intent_ref,dict):
                raise ValueError('Interrupted continuation requires an exact source step, attempt and intent reference')
            request.update(after_interrupt=True,native_intent_ref=native_intent_ref,new_context=bool(new_context))
        elif native_intent_ref is not None or new_context:
            raise ValueError('Operator context selection requires explicit after-interrupt continuation')
        if operation_id is not None:
            with budget._ledger_writer(root):
                previous = _replay(root, operation_id, 'controller', request)
                if previous is not None:
                    return previous
        action = dict(kind='continue' if continue_run else 'run')
        if resume_step is not None:
            if continue_run or ack_correction is not None or authorization_note is not None:
                raise ValueError('Exact worker resume cannot be combined with continue or correction acknowledgment')
            if type(resume_step) is not int or not 0 < resume_step <= budget.historical_step_limit(root):
                raise ValueError('An exact prepared node is required')
            path = contracts.regular(root / 'jobs' / f'{resume_step:03d}.json')
            action = dict(kind='worker_resume', step=resume_step, job_ref=contracts.record_ref(path))
            if expected_attempt is not None:action['expected_attempt']=expected_attempt
            if after_interrupt:
                action.update(after_interrupt=True,native_intent_ref=native_intent_ref,new_context=bool(new_context))
        descriptor = os.open(root / 'host-run.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if operation_id is not None:
                    with budget._ledger_writer(root):
                        previous = _replay(root, operation_id, 'controller', request)
                        if previous is not None:
                            return previous
                    raise RuntimeError('Another controller owns this instance; this exact request was not accepted')
                active = [row for row in operations(root) if row['kind'] == 'controller'
                          and row['status'] in {'accepted', 'running'} and row.get('process') is not None
                          and runtime.process_matches(row['process'], row['operation_id'])]
                if active:
                    return dict(active[-1], already_running=True)
                return dict(operation_id=None, kind='controller', status='running', already_running=True,
                            owner='existing_controller')
            if authorization_note is not None and ack_correction is None:
                raise ValueError('An authorization note requires an exact correction trigger')
            if ack_correction is not None and not continue_run:
                raise ValueError('Correction acknowledgment requires explicit continue')
            if continue_run:
                action['wait_target'] = controller._wait_continuation_target(root)
            value = _new('controller', action=action, continue_run=bool(continue_run), ack_correction=ack_correction,
                         authorization_note=authorization_note)
            with budget._ledger_writer(root):
                if operation_id is not None:
                    previous = _replay(root, operation_id, 'controller', request)
                    if previous is not None:
                        return previous
                    value.update(operation_id=operation_id, request=request)
                value['sequence'] = 1 + len(list((root / 'host-control' / 'operations').glob('*.json')))
                contracts.write(_path(root, value['operation_id']), value)
            try:
                child = subprocess.Popen([sys.executable, '-m', __name__, str(root), value['operation_id'], str(descriptor)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True, close_fds=True, pass_fds=(descriptor,))
                # The child publishes its exact identity under the same short lock.
                with budget._ledger_writer(root):
                    current = _read(root, value['operation_id'])
                    if current['status'] == 'accepted':
                        try:
                            current['process'] = runtime.process_identity(child.pid, value['operation_id'])
                        except (OSError, ValueError):
                            pass  # A very short child may already have published its final receipt.
                        contracts.write(_path(root, value['operation_id']), current)
                    value = current
            except Exception:
                value.update(status='failed', error='Detached controller could not be started')
                with budget._ledger_writer(root):
                    contracts.write(_path(root, value['operation_id']), value)
                raise
            return value
        finally:
            os.close(descriptor)


def request_budget_amendment(root, *, operation_id=None, **amendment):
    root = Path(root).resolve()
    if operation_id is not None:
        with budget._ledger_writer(root):
            previous = _replay(root, operation_id, 'budget_amend', amendment)
            if previous is not None:
                return previous
    budget._validate_amendment(root, **amendment)
    value = _new('budget_amend', amendment=amendment)
    with budget._ledger_writer(root):
        if operation_id is not None:
            previous = _replay(root, operation_id, 'budget_amend', amendment)
            if previous is not None:
                return previous
            value.update(operation_id=operation_id, request=amendment)
        value['sequence'] = 1 + len(list((root / 'host-control' / 'operations').glob('*.json')))
        contracts.write(_path(root, value['operation_id']), value)
    try:
        with controller._writer(root):
            apply_pending_budget(root)
    except RuntimeError:
        pass  # The current writer applies it before admitting a subsequent new task.
    return operation(root, value['operation_id'])


def apply_pending_budget(root):
    """Called by the existing writer at a boundary with no running original."""
    if budget.execution_blocked(root):
        return
    paths = list((Path(root) / 'host-control' / 'operations').glob('*.json'))
    paths.sort(key=lambda path: contracts.read(contracts.regular(path)).get('sequence', 0))
    for path in paths:
        with budget._ledger_writer(root):
            value = contracts.read(contracts.regular(path))
            if value['kind'] != 'budget_amend' or value['status'] not in {'accepted', 'running'}:
                continue
            value['status'] = 'running'
            contracts.write(path, value)
        try:
            result = budget._amend_locked(root, **value['amendment'], operation_id=value['operation_id'])
            value.update(status='applied', result=result)
        except Exception as error:
            value.update(status='failed', error=str(error))
        with budget._ledger_writer(root):
            contracts.write(path, value)


def _child(root, operation_id, descriptor):
    from .instance_owner import lease
    from . import correction
    root = Path(root).resolve()
    try:
        # The inherited open description is the original controller writer lock.
        info = os.fstat(descriptor)
        current = os.stat(root / 'host-run.lock', follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError('Detached controller lost the original writer lock')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with lease(root), controller._local_writer(root,descriptor=descriptor), controller._inactive_on_error(root):
            with budget._ledger_writer(root):
                value = _read(root, operation_id)
                current_process=value.get('process')
                if (value.get('operation_id')!=operation_id or value.get('kind')!='controller'
                        or current_process is not None and (current_process.get('pid')!=os.getpid()
                            or not runtime.process_matches(current_process,operation_id))):
                    raise ValueError('Detached controller lost its original operation identity')
                if value['status'] not in {'accepted', 'running'}:
                    return
                value.update(status='running', process=runtime.process_identity(os.getpid(), operation_id))
                contracts.write(_path(root, operation_id), value)
            if value['continue_run']:
                if value['ack_correction'] is not None:
                    correction.acknowledge(root, value['ack_correction'], value['authorization_note'])
                (root / 'pause.request').unlink(missing_ok=True)
            action = value.get('action', dict(kind='run'))
            if action['kind'] == 'worker_resume':
                path = contracts.regular(root / 'jobs' / f'{action["step"]:03d}.json')
                if contracts.record_ref(path) != action['job_ref']:
                    raise ValueError('Detached resume lost its exact original scientific task')
                options=dict(expected_attempt=action.get('expected_attempt'))
                if action.get('after_interrupt'):
                    options.update(after_interrupt=True,native_intent_ref=action['native_intent_ref'],
                        new_context=action['new_context'])
                result = controller._worker_resume_locked(root, action['step'],**options)
            else:
                result = (controller._run(root, continue_wait=action['wait_target'])
                    if action['kind'] == 'continue' else controller._run(root))
            apply_pending_budget(root)
            with budget._ledger_writer(root):
                value.update(status='applied', result=result)
                contracts.write(_path(root, operation_id), value)
    except BaseException as error:
        with budget._ledger_writer(root):
            value = _read(root, operation_id)
            value.update(status='failed', error=str(error))
            contracts.write(_path(root, operation_id), value)
    finally:
        os.close(descriptor)


if __name__ == '__main__':
    _child(Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]))
