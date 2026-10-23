"""Durable receipts for detached control and boundary budget amendments."""
from pathlib import Path
import fcntl
import os
import subprocess
import sys
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


def start_background(root, *, continue_run=False, ack_correction=None, authorization_note=None, resume_step=None):
    """Hand the existing single-writer lock to an independent controller process."""
    root = Path(root).resolve()
    from .instance_owner import lease
    from .protocol import require_current
    with lease(root):
        require_current(root)
        action = dict(kind='continue' if continue_run else 'run')
        if resume_step is not None:
            if continue_run or ack_correction is not None or authorization_note is not None:
                raise ValueError('Exact worker resume cannot be combined with continue or correction acknowledgment')
            if type(resume_step) is not int or not 0 < resume_step <= budget.historical_step_limit(root):
                raise ValueError('An exact prepared node is required')
            path = contracts.regular(root / 'jobs' / f'{resume_step:03d}.json')
            action = dict(kind='worker_resume', step=resume_step, job_ref=contracts.record_ref(path))
        descriptor = os.open(root / 'host-run.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
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


def request_budget_amendment(root, **amendment):
    root = Path(root).resolve()
    budget._validate_amendment(root, **amendment)
    value = _new('budget_amend', amendment=amendment)
    with budget._ledger_writer(root):
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
                result = controller._worker_resume_locked(root, action['step'])
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
