"""Administrator-only opt-in to remote retirement of legacy uncertain requests.

Not an MCP/Worker tool. This records the operator's assertion that every old
submitter has stopped; it does not change execution state or release any budget.
The ordinary cancel/abandon path must still obtain remote atomic retirement proof.
For a task diagnostic use --instance INSTANCE_NAME --run-name RUN_NAME
--diagnostic-name NAME, together with the configured --state-root and the explicit
administrator confirmation/reason. No ledger path or private Run ID is needed.
"""
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3

from ..service.engineering_diagnostics import atomic_json, read_json
from ..service.execution_submission import ExecutionSubmission
from ..service.maintenance import StateMaintenanceLock


def authorize_legacy_retirement(state_root, execution_id, *, confirm_old_submitters_stopped, reason):
    if not confirm_old_submitters_stopped or not reason.strip() or len(reason) > 2048:
        raise ValueError("Confirm all old local/remote submitters have stopped and supply a bounded administrative reason")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", execution_id):
        raise ValueError("invalid execution identity")
    state = Path(state_root).expanduser().resolve(strict=True)
    directory = state / 'execution-exchange' / execution_id
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("execution exchange directory is unavailable")
    with StateMaintenanceLock(state / 'maintenance.lock', shared_group=True).exclusive(blocking=False):
        lock = ExecutionSubmission._lock(directory / 'submission.lock')
        if lock is None:
            raise RuntimeError("A local submitter still owns this execution; stop it before administrator recovery")
        try:
            with sqlite3.connect((state / 'database/executions.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
                row = db.execute('''SELECT e.state,e.external_run_id,p.descriptor_json
                    FROM executions e LEFT JOIN execution_prepared_submissions p USING(execution_id)
                    WHERE e.execution_id=?''', (execution_id,)).fetchone()
            if row is None or row[0] not in {'created', 'authorized'} or row[1] is not None or row[2] is None:
                raise ValueError("Only an unconfirmed prepared execution can enter legacy recovery")
            path = directory / 'submission.json'
            record = read_json(path) if path.exists() else {}
            descriptor = json.loads(row[2])
            if record.get('retirement_supported') is True:
                return {'execution_id': execution_id, 'state': 'retirement_already_enabled'}
            record.update(state='unknown', may_have_submitted=True, retirement_supported=True,
                administrator_recovery={
                    'recorded_at': datetime.now(timezone.utc).isoformat(), 'uid': os.getuid(),
                    'reason': reason, 'old_submitters_stopped': True,
                    'submission_sha256': descriptor['sha256']})
            atomic_json(path, record)
            return {'execution_id': execution_id, 'state': 'remote_retirement_required', 'budget_released': False}
        finally:
            os.close(lock)


def authorize_legacy_diagnostic_retirement(state_root, ledger_path, *, run_id, name,
        confirm_old_submitters_stopped, reason):
    """Audit-only opt-in; the subsequent exact remote fence still owns release."""
    if not confirm_old_submitters_stopped or not reason.strip() or len(reason) > 2048:
        raise ValueError("Confirm all old submitters have stopped and supply a bounded administrative reason")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
        raise ValueError("invalid diagnostic identity")
    state = Path(state_root).expanduser().resolve(strict=True)
    path = Path(ledger_path).expanduser().absolute()
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(state):
        raise ValueError("Diagnostic ledger must be a regular file inside the control state root")
    with StateMaintenanceLock(state / 'maintenance.lock', shared_group=True).exclusive(blocking=False):
        lock = ExecutionSubmission._lock(path.with_suffix('.lock'))
        if lock is None:
            raise RuntimeError("A diagnostic owner still holds this ledger; stop it before recovery")
        try:
            with sqlite3.connect((state / 'database/runs.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
                owner = db.execute('SELECT state FROM runs WHERE run_id=?', (run_id,)).fetchone()
                if owner is None:
                    raise ValueError("Diagnostic owner Run is unavailable")
                if owner[0] not in {'completed', 'failed'}:
                    raise ValueError('Stop the original diagnostic Worker before legacy recovery; its Run is still active')
            ledger = read_json(path)
            reservation = ledger.get('reservations', {}).get(name)
            record = ledger.get('runs', {}).get(name) or (reservation or {}).get('record', {})
            if (reservation is None or record.get('run_id') != run_id
                    or record.get('external_run_id') is not None or record.get('submission_absent')
                    or record.get('state') in {'succeeded', 'failed', 'cancelled', 'collected'}):
                raise ValueError("Only an exact unconfirmed diagnostic can enter legacy recovery")
            if reservation.get('retirement_supported') is True:
                return {'state': 'retirement_already_enabled', 'budget_released': False}
            from ..schema.execution import LocalFileDescriptor
            descriptor = LocalFileDescriptor.model_validate_json(json.dumps(reservation['submission']), strict=True)
            reservation.update(retirement_supported=True, administrator_recovery={
                'recorded_at': datetime.now(timezone.utc).isoformat(), 'uid': os.getuid(),
                'reason': reason, 'old_submitters_stopped': True, 'run_id': run_id,
                'submission_sha256': descriptor.sha256})
            atomic_json(path, ledger)
            return {'state': 'remote_retirement_required', 'budget_released': False}
        finally:
            os.close(lock)


def authorize_named_diagnostic_retirement(state_root, *, instance, run_name, name,
        confirm_old_submitters_stopped, reason):
    """Resolve exact frozen names read-only; never select a ledger by recency."""
    from ..service.run_records import parse_inputs
    from ..schema.common import canonical_sha256
    state = Path(state_root).expanduser().resolve(strict=True)
    with sqlite3.connect((state / 'database/scheduler-bindings.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        bound = db.execute('''SELECT b.object_id,i.instance_id FROM scheduler_instances i
            JOIN scheduler_bindings b ON b.instance=i.instance_id AND b.namespace='run'
            WHERE i.name=? AND i.state='active' AND b.name=?''', (instance, run_name)).fetchone()
    if bound is None:
        raise ValueError('Exact Run name is unavailable in this active instance')
    inputs, seen, current, operation = [], set(), bound[0], None
    with sqlite3.connect((state / 'database/runs.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        while current and current not in seen and len(seen) < 128:
            seen.add(current)
            row = db.execute('''SELECT inputs_json,operation_id,draft_from_run_id,resume_from_run_id
                FROM runs WHERE run_id=? AND instance_id=?''', (current, bound[1])).fetchone()
            if row is None or operation is not None and row[1] != operation:
                raise ValueError('Run binding or recovery lineage does not belong to the selected instance/task')
            operation = row[1]
            inputs.extend(parse_inputs(row[0]))
            current = row[2] or row[3]
    if current:
        raise ValueError('Recovery lineage is cyclic or exceeds its bound')
    subjects = next(([item.artifact_ref for item in inputs if item.port_name == port]
        for port in ('research_objective', 'scientific_skeleton', 'experiment_plan')
        if any(item.port_name == port for item in inputs)), [])
    paths = [state / 'local-tcad-debug/budgets' / (canonical_sha256({'subject': subject}) + '.json')
        for subject in subjects]
    matches = []
    for path in dict.fromkeys(paths):
        if path.is_symlink() or not path.is_file():
            continue
        ledger = read_json(path)
        reservation = ledger.get('reservations', {}).get(name)
        record = ledger.get('runs', {}).get(name) or (reservation or {}).get('record', {})
        if record.get('run_id') == bound[0] and reservation is not None:
            matches.append(path)
    if len(matches) != 1:
        raise ValueError('Exact owner/name has no unique diagnostic reservation in its frozen subjects')
    result = authorize_legacy_diagnostic_retirement(state, matches[0], run_id=bound[0], name=name,
        confirm_old_submitters_stopped=confirm_old_submitters_stopped, reason=reason)
    return {**result, 'instance': instance, 'owner_name': run_name, 'diagnostic_name': name,
        'next_request': {'name': 'run_diagnostic', 'surface': 'execution', 'arguments': {
            'name': run_name, 'diagnostic_name': name, 'action': 'cancel'}}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root', type=Path, required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--execution-id')
    target.add_argument('--diagnostic-ledger', type=Path)
    target.add_argument('--run-name')
    parser.add_argument('--instance')
    parser.add_argument('--diagnostic-name')
    parser.add_argument('--run-id')
    parser.add_argument('--confirm-old-submitters-stopped', action='store_true', required=True)
    parser.add_argument('--reason', required=True)
    args = parser.parse_args(argv)
    options = dict(confirm_old_submitters_stopped=args.confirm_old_submitters_stopped, reason=args.reason)
    if args.run_name is not None:
        if not args.instance or not args.diagnostic_name or args.run_id:
            parser.error('named diagnostic recovery requires --instance and --diagnostic-name, without --run-id')
        result = authorize_named_diagnostic_retirement(args.state_root, instance=args.instance,
            run_name=args.run_name, name=args.diagnostic_name, **options)
    elif args.diagnostic_ledger is not None:
        if not args.diagnostic_name or not args.run_id:
            parser.error('diagnostic recovery requires --diagnostic-name and --run-id')
        result = authorize_legacy_diagnostic_retirement(args.state_root, args.diagnostic_ledger,
            run_id=args.run_id, name=args.diagnostic_name, **options)
    else:
        if args.diagnostic_name or args.run_id or args.instance:
            parser.error('diagnostic identity requires --diagnostic-ledger')
        result = authorize_legacy_retirement(args.state_root, args.execution_id, **options)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
