"""Thin trusted-local orchestration of the existing bounded TCAD debug adapter."""

from __future__ import annotations

import hashlib
import fcntl
import json
import re
import uuid
import time
from dataclasses import asdict
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path

from scidiscovery.artifact_agent.operation_tool_context import OperationToolContext
from scidiscovery.plugin_runtime.submission import SubmissionNotCreated
from scidiscovery.operation_contract import contract_diagnostic
from scidiscovery.artifact_agent.schema.common import canonical_json, canonical_sha256
from scidiscovery.artifact_agent.schema.role_result import parse_role_result
from scidiscovery.plugin_runtime.workspace import (
    WorkspaceError,
    write_control_workspace_file,
)
from scidiscovery.plugin_runtime.diagnostics import (
    RunCheckerError,
    RunOutputError,
)

from .debug_contract import (
    DEVELOPMENT_ARTIFACT_LIMIT_BYTES,
    TCADDebugError,
    TCADDebugSource,
    TCADDevelopmentDebugAdapter,
)
from .debug_adapter import _development_limits
from .execution_policy import DebugPolicy
from .project_packager import DeckProjectDraft, project_debug_sha256


_RUN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MODES = frozenset({"preflight", "smoke", "initialization"})
_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "collected"})
_STATES = frozenset({"accepted", "running", "cancelling", *_TERMINAL})
def debug_tool_description() -> str:
    return (
        "Submit or poll one TCAD development diagnostic under administrator-configured mode, "
        "attempt, wall-time, storage and delivery limits; never scientific evidence. "
        "Responses expose the configured budget scope and each job reservation. Renaming a job "
        "does not reset the budget; repeating a name polls the same submission. "
        "For initialization select declared output_names. Read details_path for retained "
        "diagnostics and file identities; file bytes are not returned in this summary."
    )


def _debug_policy(context):
    return DebugPolicy.model_validate_json(json.dumps(context.state["debug_policy"]), strict=True)


def _continuation_error(code, message, *, repairable=True, path="$.name"):
    from scidiscovery.operation_contract import DiagnosticError, contract_diagnostic
    return DiagnosticError(message, details=(contract_diagnostic(code, phase="tool_execution",
        affected_action="tool_call", path=path, repairable=repairable, message=message),))


def _settle_wall_usage(ledger, record, elapsed):
    """Replace one diagnostic reservation once, using trusted terminal timing."""
    import math
    if "actual_wall_seconds" in record or type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
        return
    actual = int(math.ceil(elapsed))
    reserved = record.get("reserved_wall_seconds")
    if type(reserved) is not int:
        return
    ledger["reserved_wall_seconds"] += actual - reserved
    record["actual_wall_seconds"] = actual


def _budget_records(context, ledger):
    scope = _debug_policy(context).budget_scope
    if "budget_ledgers" in ledger:
        records = {}
        for path, original in ledger["budget_ledgers"].items():
            if scope == "scientific_subject" and path != ledger["budget_subject_path"]:
                continue
            records.update({(path, name): item["record"] for name, item in original.get("reservations", {}).items()})
            records.update({(path, name): record for name, record in original.get("runs", {}).items()})
        for name, location in ledger["budget_record_locations"].items():
            if location in records:
                records[location] = ledger["runs"].get(name) or ledger["reservations"][name]["record"]
        return {key: record for key, record in records.items() if scope == "scientific_subject"
            or record.get("run_id") in ledger["budget_task_run_ids"]}
    records = {name: item["record"] for name, item in ledger.get("reservations", {}).items()}
    records.update(ledger.get("runs", {}))
    return {name: record for name, record in records.items()
        if scope == "scientific_subject" or context.recovery_authorized(record.get("run_id", ""))}


def debug_budget(context, ledger=None):
    ledger = context.state if ledger is None else ledger
    policy = _debug_policy(context)
    records = _budget_records(context, ledger)
    actual = sum(item.get("actual_wall_seconds", 0) for item in records.values())
    reserved = sum(item.get("reserved_wall_seconds", 0) for item in records.values()
        if "actual_wall_seconds" not in item)
    # Preserve unpaired historical charges only when that shared scope is selected.
    if policy.budget_scope == "scientific_subject":
        shared = ledger.get("budget_ledgers", {}).get(ledger.get("budget_subject_path"), ledger)
        reserved = max(reserved, shared.get("reserved_wall_seconds", 0) - actual)
    used = actual + reserved
    count = sum(not item.get("submission_absent", False) for item in records.values())
    remaining = max(0, policy.total_wall_seconds - used)
    return {"scope": policy.budget_scope, "total_wall_seconds": policy.total_wall_seconds,
        "used_wall_seconds": used, "actual_wall_seconds": actual, "reserved_wall_seconds": reserved,
        "remaining_wall_seconds": remaining, "max_runs": policy.max_runs, "created_runs": count,
        "remaining_runs": max(0, policy.max_runs - count), "run_remaining_seconds": context.remaining_seconds,
        "effective_wall_seconds": max(0, min(remaining, context.remaining_seconds))}


def _budget_error(context, code, setting):
    budget = debug_budget(context)
    return _continuation_error(code,
        f"No new diagnostic was submitted. Diagnostic budget scope={budget['scope']}: "
        f"actual={budget['actual_wall_seconds']} s, reserved={budget['reserved_wall_seconds']} s, "
        f"limit={budget['total_wall_seconds']} s; attempts={budget['created_runs']}/{budget['max_runs']}. "
        "Read status or finish/cancel an existing diagnostic to settle its reservation. "
        f"If confirmed usage exhausts the allowance, ask the administrator to increase debug.{setting} "
        "for this scope; renaming or recovering this task does not reset usage. "
        "An expired parent Run requires task recovery, not a larger diagnostic limit.", repairable=False)


def debug_response(context: OperationToolContext, run_name: str, response: dict) -> dict:
    runs = context.state.get("runs", {})
    result = {**response, "budget": debug_budget(context)}
    record = runs.get(run_name)
    if record is not None:
        result["reserved_wall_seconds_for_run"] = record["reserved_wall_seconds"]
        result["delivery_budget"] = record.get("delivery_budget")
    if len(canonical_json(result)) > _debug_policy(context).max_response_bytes:
        raise TCADDebugError("TCAD debug diagnostic response exceeds its bound")
    return result


def debug_summary(context, run_name, response):
    """Keep the complete engineering snapshot in this Run, not in every poll."""
    full = debug_response(context, run_name, response)
    path = write_control_workspace_file(context.workspace,
        Path(f"deck/reports/response-{run_name}.json"), canonical_json(full),
        replace=True, mode=0o400, create_parents=True)
    result = {key: full[key] for key in ("run_name", "mode", "state", "phase", "development_only",
        "scientific_claim_admissible", "diagnostic_layer", "summary", "exit_code", "diagnostics",
        "source_diagnostic", "log_relative_path", "delivery_budget", "budget", "reserved_wall_seconds_for_run", "missing_outputs") if key in full}
    if isinstance(full.get("progress"), dict):
        from scidiscovery.plugin_runtime.experiment import progress_summary
        result["progress"] = progress_summary(full["progress"])
    result.update(details_path=str(path), output_count=len(full.get("outputs", [])))
    return result


class LocalTCADDebugService:
    """Keep only process-local diagnostic bindings; Run owns scientific state."""

    def __init__(self, *, adapter: TCADDevelopmentDebugAdapter, exchange_root: Path | str,
                 runtime_context=None) -> None:
        self.adapter = adapter
        self.runtime_context = runtime_context
        self.exchange_root = Path(exchange_root).expanduser().absolute()
        self.exchange_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.exchange_root.is_symlink() or not self.exchange_root.is_dir():
            raise ValueError("local TCAD debug exchange must be a real directory")

    def run(
        self, context: OperationToolContext, *, run_name: str, mode: str, output_names: list[str] | None = None
    ) -> dict[str, object]:
        subject = None
        for alias in ("research_objective", "scientific_skeleton", "experiment_plan"):
            try:
                subject = context.input_ref(alias)
                break
            except (KeyError, ValueError):
                continue
        if subject is None:
            raise TCADDebugError("debug budget requires an immutable scientific subject binding")
        origin, _, _ = self._context_budget_paths(context, subject)
        current = self._subject_path(subject)
        primary = current
        primary.parent.mkdir(exist_ok=True, mode=0o700)
        with ExitStack() as stack:
            # Serialize admission by the sealed task root, then refresh its
            # family so a newly created recovery branch cannot escape counting.
            task_lock = stack.enter_context((primary.parent / (canonical_sha256({"task":origin}) + ".task.lock")).open("a+"))
            fcntl.flock(task_lock.fileno(), fcntl.LOCK_EX)
            _, task_ids, paths = self._context_budget_paths(context, subject)
            for path in sorted(set(paths)):
                lock = stack.enter_context(path.with_suffix(".lock").open("a+"))
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            ledgers = {str(path): json.loads(path.read_bytes()) for path in paths if path.exists()}
            ledgers.setdefault(str(primary), {})
            runs, reservations, locations = self._owned_budget(context, ledgers, run_name=run_name)
            context.state.update(runs=runs, reservations=reservations,
                reserved_wall_seconds=sum(item.get("reserved_wall_seconds", 0) for item in ledgers.values()),
                budget_ledgers=ledgers, budget_record_locations=locations, budget_task_run_ids=task_ids,
                budget_subject_path=str(current), budget_ledger_path=str(primary))
            # Administrator updates apply without erasing historical consumption.
            context.state["debug_policy"] = self.adapter.policy.model_dump(mode="json")
            try:
                response = self._run_locked(context, run_name=run_name, mode=mode, output_names=output_names)
                if getattr(context, 'collect_only', False):
                    record = context.state['runs'][run_name]
                    if record.get('state') == 'collected' and not record.get('control_delivery'):
                        root = self._receipt_directory(context, run_name, record['run_id'])
                        receipt = json.loads((root / 'receipt.json').read_bytes())
                        files = [{**item, 'path': root / item['sha256']} for item in receipt['proof']['files']]
                        record['control_delivery'] = context.state['control_publish'](receipt, files)
                return debug_response(context, run_name, response)
            finally:
                self._save_budget(context)

    def _subject_path(self, subject):
        return self.exchange_root / "budgets" / (canonical_sha256({"subject": subject}) + ".json")

    def _context_budget_paths(self, context, subject):
        scope = getattr(context, "recovery_scope", None)
        origin, task_ids, inputs = scope() if callable(scope) else (getattr(context, "run_id", ""), (getattr(context, "run_id", ""),), ())
        anchors = next(([ref for port, ref in inputs if port == alias]
            for alias in ("research_objective", "scientific_skeleton", "experiment_plan")
            if any(port == alias for port, _ in inputs)), [])
        return origin, task_ids, tuple(dict.fromkeys(self._subject_path(ref) for ref in (*anchors, subject)))

    @staticmethod
    def _owned_budget(context, ledgers, *, run_name):
        runs, reservations, locations = {}, {}, {}
        authorized = getattr(context, "recovery_authorized", lambda _: True)
        candidates = {}
        for path, ledger in ledgers.items():
            for name in dict.fromkeys((*ledger.get("reservations", {}), *ledger.get("runs", {}))):
                record = ledger.get("runs", {}).get(name) or ledger["reservations"][name]["record"]
                candidates.setdefault(name, []).append((authorized(record.get("run_id", "")), path))
        for name, entries in candidates.items():
            owned = [path for belongs, path in entries if belongs]
            sources = owned or [path for _, path in entries]
            if len(sources) != 1:
                if name == run_name:
                    raise _continuation_error("tcad_debug_name_ambiguous",
                        "This diagnostic name identifies multiple original submissions. Retain both identities and ask the administrator to repair their names before polling or submitting a replacement.", repairable=False)
                continue  # All original charges remain in the complete ledger view.
            path = sources[0]
            ledger = ledgers[path]
            locations[name] = (path, name)
            if name in ledger.get("runs", {}):
                runs[name] = deepcopy(ledger["runs"][name])
            if name in ledger.get("reservations", {}):
                reservations[name] = deepcopy(ledger["reservations"][name])
        return runs, reservations, locations

    def experiment_diagnostic_status(self, run_ids, inputs, *, name, recovery_route=None):
        """Control takeover remains available after the owning Worker terminates."""
        from types import SimpleNamespace
        subjects = {alias: next((item.artifact_ref for item in inputs if item.port_name == alias), None)
            for alias in ("research_objective", "scientific_skeleton", "experiment_plan")}
        def input_ref(alias):
            if subjects.get(alias) is None:
                raise KeyError(alias)
            return subjects[alias]
        context = SimpleNamespace(input_ref=input_ref, recovery_authorized=lambda owner: owner in run_ids,
            task_scoped=True, state={'diagnostic_recovery_route': recovery_route},
            recovery_scope=lambda: ("", tuple(run_ids), tuple((item.port_name, item.artifact_ref) for item in inputs)))
        return self.status(context, run_name=name)

    def experiment_diagnostic_collect(self, run_ids, inputs, *, name, run_id,
                                      operation_id, seconds, publish):
        """Collect one existing exact diagnostic; never admit a solver request.

        Reuse retained receipts on retries. Scientific task output is untouched;
        the callback seals an independent control delivery before it is marked
        delivered in the original budget ledger.
        """
        from types import SimpleNamespace
        subjects = {alias: next((item.artifact_ref for item in inputs if item.port_name == alias), None)
            for alias in ("research_objective", "scientific_skeleton", "experiment_plan")}
        def input_ref(alias):
            if subjects.get(alias) is None:
                raise KeyError(alias)
            return subjects[alias]
        workspace = self.exchange_root / 'takeover' / canonical_sha256({'run': run_id, 'name': name})
        workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        context = SimpleNamespace(input_ref=input_ref, recovery_authorized=lambda owner: owner in run_ids,
            recovery_scope=lambda: (run_id, tuple(run_ids), tuple((item.port_name, item.artifact_ref) for item in inputs)),
            run_id=run_id, operation_id=operation_id, workspace=workspace, state={},
            remaining_seconds=seconds, record_activity=lambda _: None, collect_only=True)
        # Mode comes from the owned immutable submission, never the caller.
        observed = self.experiment_diagnostic_status(run_ids, inputs, name=name)
        if observed.get('state') == 'not_created' or observed.get('submission') == 'not_submitted':
            return observed
        context.state['control_publish'] = publish
        self.run(context, run_name=name, mode=observed['mode'])
        return self.experiment_diagnostic_status(run_ids, inputs, name=name)

    def experiment_pending_diagnostics(self, run_ids, inputs):
        pending = []
        self.experiment_activity(run_ids, inputs, pending=pending)
        return pending

    def experiment_activity(self, run_ids, inputs, *, cancel=False, name=None, pending=None):
        """Observe/cancel all frozen recovery subjects without resetting usage."""
        subjects = next(([item.artifact_ref for item in inputs if item.port_name == port]
            for port in ("research_objective", "scientific_skeleton", "experiment_plan")
            if any(item.port_name == port for item in inputs)), [])
        paths = sorted({self._subject_path(subject) for subject in subjects
            if self._subject_path(subject).exists()})
        with ExitStack() as stack:
            for path in paths:
                lock = stack.enter_context(path.with_suffix(".lock").open("a+"))
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX if cancel else fcntl.LOCK_SH)
            active = False
            if name is not None:
                matches = 0
                for path in paths:
                    ledger = json.loads(path.read_bytes())
                    records = {key: value["record"] for key, value in ledger.get("reservations", {}).items()}
                    records.update(ledger.get("runs", {}))
                    matches += sum(record.get("run_id") in run_ids and key == name
                        for key, record in records.items())
                if matches > 1:
                    raise _continuation_error("tcad_debug_name_ambiguous",
                        "Cancellation names multiple submissions in the recovery chain. Retain both identities and ask the administrator to repair their names before cancellation.", repairable=False)
            for path in paths:
                active = self._ledger_activity(run_ids, path, cancel=cancel, name=name, pending=pending) or active
            return active

    def _ledger_activity(self, run_ids, path, *, cancel, name, pending):
        from scidiscovery.artifact_agent.schema.execution import LocalFileDescriptor
        from scidiscovery.plugin_runtime.workspace import atomic_json
        active = False
        ledger = json.loads(path.read_bytes())
        records = ledger.setdefault("runs", {})
        for alias, reservation in ledger.get("reservations", {}).items():
            record = records.get(alias, reservation["record"])
            if record.get("run_id") not in run_ids or (name is not None and alias != name):
                continue
            if record.get("state") in _TERMINAL:
                continue
            external = record.get("external_run_id")
            if external is None:
                try:
                    found = self.adapter.lookup_submission(LocalFileDescriptor.model_validate_json(json.dumps(reservation["submission"]), strict=True))
                except Exception:
                    if pending is None or cancel:
                        raise  # A failed query cannot grant cancellation or absence.
                    active = True
                    pending.append(contract_diagnostic("experiment_diagnostic_submission_unknown", phase="protocol",
                        affected_action="submit", repairable=False, path="$.diagnostics." + alias,
                        message=f"Diagnostic {alias!r} submission cannot be observed while its service is unavailable. Its exact identity and allowance remain reserved; only a limited blocked/inconclusive report can be sealed. Root can query or cancel this exact diagnostic through run_diagnostic after service recovery."))
                    continue
                if found is None and cancel:
                    # lookup absence alone is never sufficient. Retirement fences
                    # any delayed submit under the executor's creation lock.
                    retire = getattr(self.adapter, "retire_submission", None)
                    if not callable(retire):
                        raise _continuation_error('tcad_debug_retirement_protocol_unavailable',
                            f"Diagnostic {alias!r} requires a backend supporting exact-submission retirement. Its allowance remains reserved.", repairable=False)
                    if reservation.get("retirement_supported") is not True:
                        raise _continuation_error('tcad_debug_legacy_retirement_confirmation_required',
                            f"Diagnostic {alias!r} predates the retirement protocol. An administrator must stop all old local and remote submitters and use the named submission_recovery entry returned by Root run_diagnostic status; then retry this exact cancellation. Its allowance remains reserved.", repairable=False)
                    try:
                        validator = getattr(self.adapter, 'validate_submission_protocol', None)
                        if callable(validator):
                            validator()
                    except Exception as error:
                        raise _continuation_error('tcad_debug_retirement_protocol_unavailable',
                            f"Diagnostic {alias!r} backend retirement protocol is unavailable. Restore the backend protocol and retry this exact cancellation; its allowance remains reserved.", repairable=False) from error
                    try:
                        found = retire(LocalFileDescriptor.model_validate_json(json.dumps(reservation["submission"]), strict=True))
                    except Exception as error:
                        busy = str(error).startswith('Submission is still in progress;')
                        raise _continuation_error('tcad_debug_retirement_busy' if busy else 'tcad_debug_retirement_failed',
                            f"Diagnostic {alias!r} exact retirement {'is busy' if busy else 'was not confirmed'}. Retry Root run_diagnostic cancel for its original owner/name after service recovery; its allowance remains reserved.", repairable=False) from error
                    if found is None:
                        record = {**record, "state": "cancelled", "submission_absent": True,
                            "actual_wall_seconds": 0, "absence_reason": "retired"}
                        before = records.get(alias, reservation["record"])
                        ledger["reserved_wall_seconds"] = ledger.get("reserved_wall_seconds", 0) - before.get(
                            "actual_wall_seconds", before.get("reserved_wall_seconds", 0))
                        records[alias] = record
                        reservation["record"] = record
                        continue
                if found is None:
                    active = True
                    if pending is not None:
                        pending.append(contract_diagnostic("experiment_diagnostic_submission_unknown", phase="protocol",
                            affected_action="submit", repairable=False, path="$.diagnostics." + alias,
                            message=f"Diagnostic {alias!r} has an uncertain submission. Read worker_experiment_debug(action='status', name={alias!r}) after service recovery, then finish or cancel the confirmed job; its allowance remains reserved."))
                    continue
                external, state = found
                record = {**record, "external_run_id": external, "state": _state(state)}
            if record["state"] not in _TERMINAL:
                # The persisted reservation is not a live observation. An owner
                # may have exited before recording the diagnostic's terminal state.
                record = {**record, "state": _state(self.adapter.status(external))}
            if cancel and record["state"] not in _TERMINAL:
                record["state"] = _state(self.adapter.cancel(external))
            active = active or record["state"] not in _TERMINAL
            if pending is not None and record["state"] not in _TERMINAL:
                pending.append(contract_diagnostic("experiment_diagnostics_pending", phase="protocol",
                    affected_action="submit", repairable=True,
                    message=f"Diagnostic {alias!r} is {record['state']}. Read worker_experiment_debug(action='status', name={alias!r}); finish it or call action='cancel' with the same name, then resubmit the unchanged report."))
            if cancel:
                reader = getattr(self.adapter, "status_details", None)
                if record["state"] in _TERMINAL and callable(reader):
                    try:
                        observed = reader(record["external_run_id"])
                    except Exception:
                        observed = {}  # Keep its known reservation without usage proof.
                    if observed.get("state") in _TERMINAL:
                        elapsed = (observed.get("consumed_budget") or {}).get("wall_time_seconds")
                        if elapsed is None:
                            elapsed = (observed.get("progress") or {}).get("elapsed_seconds")
                        _settle_wall_usage(ledger, record, elapsed)
                records[alias] = record
        if cancel:
            atomic_json(path, ledger)
        return active

    def status(self, context, *, run_name):
        """Observe an exact diagnostic name without policy discovery, submission or collection."""
        if _RUN_NAME.fullmatch(run_name) is None:
            raise TCADDebugError("TCAD development debug name is invalid")
        subject = None
        for alias in ("research_objective", "scientific_skeleton", "experiment_plan"):
            try:
                subject = context.input_ref(alias)
                break
            except (KeyError, ValueError):
                continue
        if subject is None:
            raise TCADDebugError("debug status requires an immutable scientific subject binding")
        _, _, paths = self._context_budget_paths(context, subject)
        paths = tuple(path for path in paths if path.exists())
        absent = {"name": run_name, "state": "not_created", "submission": "not_submitted"}
        with ExitStack() as stack:
            for path in sorted(set(paths)):
                if path.exists():
                    lock = stack.enter_context(path.with_suffix(".lock").open("a+"))
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX if getattr(context, "task_scoped", False) else fcntl.LOCK_SH)
            ledgers = {str(path): json.loads(path.read_bytes()) for path in paths if path.exists()}
            runs, reservations, locations = self._owned_budget(context, ledgers, run_name=run_name)
            ledger = {"runs": runs, "reservations": reservations}
            reservation = ledger.get("reservations", {}).get(run_name)
            record = ledger.get("runs", {}).get(run_name)
            if record is None and reservation is None:
                return absent
            record = record or reservation["record"]
            if getattr(context, "task_scoped", False) and not context.recovery_authorized(record.get("run_id", "")):
                return absent
            if record.get("submission_absent"):
                return {"name": run_name, "state": "cancelled", "submission": "not_submitted", "allowance_reserved": False}
            external = record.get("external_run_id")
            state = record["state"]
            details = {}
            def persist_observation(external, state, details):
                if not getattr(context, "task_scoped", False) or external is None:
                    return
                from scidiscovery.plugin_runtime.workspace import atomic_json
                path, alias = locations[run_name]
                original = ledgers[path]
                retained_state = 'collected' if record.get('state') == 'collected' and state in _TERMINAL else _state(state)
                record.update(external_run_id=external, state=retained_state)
                if state in _TERMINAL:
                    elapsed = (details.get("consumed_budget") or {}).get("wall_time_seconds")
                    if elapsed is None:
                        elapsed = (details.get("progress") or {}).get("elapsed_seconds")
                    _settle_wall_usage(original, record, elapsed)
                original.setdefault("runs", {})[alias] = record
                if alias in original.get("reservations", {}):
                    original["reservations"][alias]["record"] = record
                atomic_json(Path(path), original)
            try:
                if external is None and state not in _TERMINAL:
                    from scidiscovery.artifact_agent.schema.execution import LocalFileDescriptor
                    found = self.adapter.lookup_submission(LocalFileDescriptor.model_validate_json(
                        json.dumps(reservation["submission"]), strict=True))
                    if found is None:
                        code = 'tcad_debug_legacy_retirement_confirmation_required' if reservation.get('retirement_supported') is not True else 'tcad_debug_exact_retirement_required'
                        validator = getattr(self.adapter, 'validate_submission_protocol', None)
                        try:
                            if not callable(getattr(self.adapter, 'retire_submission', None)):
                                raise RuntimeError('retirement unavailable')
                            if callable(validator):
                                validator()
                        except Exception:
                            code = 'tcad_debug_retirement_protocol_unavailable'
                        route = getattr(context, 'state', {}).get('diagnostic_recovery_route')
                        recovery = route(record['run_id'], run_name) if callable(route) else {}
                        return {"name": run_name, "mode": record['mode'], "state": "submission_unknown", "submission": "unknown",
                            "allowance_reserved": True,
                            'recovery': {**recovery, 'code': code},
                            "instruction": "The exact submission is not yet visible. Root can cancel its exact original owner/name after the indicated recovery; budget stays reserved until persistent exact retirement is confirmed."}
                    external, state = found
                    # Persist the recovered identity even if the next remote
                    # status query fails. It must never be refunded as absent.
                    persist_observation(external, state, {})
                if state not in _TERMINAL or (getattr(context, "task_scoped", False)
                        and external is not None and "actual_wall_seconds" not in record):
                    reader = getattr(self.adapter, "status_details", None)
                    details = reader(external) if callable(reader) else {"state": self.adapter.status(external)}
                    state = details["state"]
                persist_observation(external, state, details)
            except Exception as error:
                from scidiscovery.operation_contract import DiagnosticError, contract_diagnostic
                message = ("Diagnostic status could not be confirmed. This read-only query did not submit a job. "
                    "Keep the same diagnostic name and retry status after service recovery; do not start another diagnostic.")
                raise DiagnosticError(message, details=(contract_diagnostic("tcad_debug_status_unknown",
                    phase="tool_execution", affected_action="tool_call", message=message),)) from error
            response = record.get("response", {})
            from scidiscovery.plugin_runtime.experiment import progress_summary
            progress = details.get("progress", response.get("progress"))
            return {"name": run_name, "mode": record["mode"], "state": record["state"] if getattr(context, "task_scoped", False) else _state(state),
                "submission": "submitted", "allowance_reserved": "actual_wall_seconds" not in record,
                **({'delivery_pending': not bool(record.get('control_delivery'))}
                   if record.get('control_collection') else {}),
                **({'delivery': record['control_delivery']} if record.get('control_delivery') else {}),
                **({"progress": progress_summary(progress)} if isinstance(progress, dict) else {}),
                **{key: response[key] for key in ("summary", "exit_code", "missing_outputs") if key in response}}

    @staticmethod
    def _save_budget(context):
        """Write each owned record back to its original ledger; never copy history."""
        from scidiscovery.plugin_runtime.workspace import atomic_json
        ledgers = context.state["budget_ledgers"]
        locations = context.state["budget_record_locations"]
        primary = context.state["budget_ledger_path"]
        runs, reservations = context.state["runs"], context.state["reservations"]
        for name in dict.fromkeys((*reservations, *runs)):
            if name not in locations:
                key = name
                ledger = ledgers[primary]
                if key in ledger.get("runs", {}) or key in ledger.get("reservations", {}):
                    raise TCADDebugError("diagnostic storage identity is already occupied")
                locations[name] = (primary, key)
            path, key = locations[name]
            ledger = ledgers[path]
            before = ledger.get("runs", {}).get(key) or ledger.get("reservations", {}).get(key, {}).get("record", {})
            after = runs.get(name) or reservations[name]["record"]
            charge = lambda record: record.get("actual_wall_seconds", record.get("reserved_wall_seconds", 0))
            ledger["reserved_wall_seconds"] = ledger.get("reserved_wall_seconds", 0) + charge(after) - charge(before)
            if name in runs:
                ledger.setdefault("runs", {})[key] = deepcopy(runs[name])
            if name in reservations:
                ledger.setdefault("reservations", {})[key] = deepcopy(reservations[name])
        ledgers[primary]["debug_policy"] = context.state["debug_policy"]
        for path, ledger in ledgers.items():
            atomic_json(Path(path), ledger)
        context.state["reserved_wall_seconds"] = sum(ledger.get("reserved_wall_seconds", 0) for ledger in ledgers.values())

    def _run_locked(self, context, *, run_name, mode, output_names):
        context.state.setdefault("debug_policy", self.adapter.policy.model_dump(mode="json"))
        call_started = time.monotonic()
        if _RUN_NAME.fullmatch(run_name) is None or mode not in _MODES:
            raise TCADDebugError("TCAD development debug request is invalid")
        if output_names and mode != "initialization":
            raise TCADDebugError("output_names is only available for initialization")
        selected = tuple(sorted(set(output_names or ())))
        runs = context.state.setdefault("runs", {})
        if not isinstance(runs, dict):
            raise TCADDebugError("local TCAD debug state is invalid")
        record = runs.get(run_name)
        reservations = context.state.setdefault("reservations", {})
        if record is None and run_name in reservations:
            from scidiscovery.artifact_agent.schema.execution import LocalFileDescriptor
            pending = reservations[run_name]
            if (self._receipt_directory(context, run_name, pending["record"]["run_id"]) / "receipt.json").is_file():
                record = pending["record"]
            else:
                found = self.adapter.lookup_submission(LocalFileDescriptor.model_validate_json(
                    json.dumps(pending["submission"]), strict=True))
                if found is None:
                    raise _continuation_error("tcad_debug_submission_unknown",
                        "The exact reserved submission is not yet visible; an earlier submit may still be processing. Its identity and allowance remain reserved. Retry status with the same name after service recovery, then finish or cancel the confirmed job; do not start a replacement.", repairable=False)
                record = {**pending["record"], "external_run_id": found[0], "state": _state(found[1])}
            runs[run_name] = record
        if record is None and getattr(context, 'collect_only', False):
            raise _continuation_error('tcad_debug_not_created',
                'No owned diagnostic exists with this exact name; collection never submits work.')
        if record is not None and getattr(context, 'collect_only', False):
            record['control_collection'] = True
        if record is None:
            # A new name cannot be used to bypass an uncertain original request.
            pending_records = ((alias, runs.get(alias, item["record"])) for alias, item in reservations.items())
            for alias, item in pending_records:
                if (not item.get("submission_absent")
                    and item.get("external_run_id") is None
                    and item.get("state") not in _TERMINAL
                    and ("experiment_implementation_ref" not in context.state
                        or item.get("implementation_ref") == context.state["experiment_implementation_ref"])
                    ):
                    route = context.state.get('diagnostic_recovery_route')
                    recovery = route(item['run_id'], alias) if callable(route) else {}
                    owner = recovery.get('owner_name')
                    instruction = (f"Root must call run_diagnostic(surface='execution', name={owner!r}, diagnostic_name={alias!r}, action='status'), then follow its exact cancellation/recovery route. "
                        if owner else f"Root must resolve the original owning Run and call run_diagnostic for diagnostic_name={alias!r}. ")
                    raise _continuation_error("tcad_debug_submission_unknown",
                        f"No new diagnostic was submitted. Original diagnostic {alias!r} is uncertain. " + instruction + "Its allowance remains reserved.", repairable=False)
            if debug_budget(context)["remaining_runs"] == 0:
                raise _budget_error(context, "tcad_debug_attempts_exhausted", "max_runs")
            self._refresh_budget_usage(context)
            record = self._start(context, run_name, mode, selected)
            runs[run_name] = record
        if not isinstance(record, dict) or record.get("mode") != mode:
            raise TCADDebugError("TCAD debug run_name is bound to another mode")
        if record.get("submission_absent"):
            raise _continuation_error("tcad_debug_not_submitted",
                "Control confirmed this diagnostic was never submitted and retired its reservation. Use a new diagnostic name if it is still needed; this name remains retired.", repairable=True)
        if output_names is not None and tuple(record.get("output_names", ())) != selected:
            raise TCADDebugError("debug run_name is bound to another output selection; poll without output_names")
        if "experiment_implementation_ref" in context.state and record.get("implementation_ref") != context.state["experiment_implementation_ref"]:
            raise _continuation_error("tcad_debug_implementation_mismatch",
                "This diagnostic name belongs to a different sealed implementation; use its original implementation or a new diagnostic name.")
        if isinstance(record.get("response"), dict) or (self._receipt_directory(context, run_name, record["run_id"]) / "receipt.json").is_file():
            return self._restore_receipt(context, run_name, record, validate=not getattr(context, "collect_only", False))
        if not context.recovery_authorized(record["run_id"]):
            raise _continuation_error("tcad_debug_owner_mismatch",
                "This diagnostic has no retained result and belongs to another task. Use read-only status; recover the owning task to collect or cancel it.")
        state = _state(str(record["state"]))
        details = {}
        if state not in _TERMINAL:
            try:
                reader = getattr(self.adapter, "status_details", None)
                details = (reader(str(record["external_run_id"])) if callable(reader)
                           else {"state": self.adapter.status(str(record["external_run_id"]))})
                state = _state(str(details["state"]))
            except Exception as error:
                raise _continuation_error("tcad_debug_status_unknown",
                    "The executor could not report this diagnostic's state. Query status for the same name after service recovery; do not start a replacement.", repairable=False) from error
            record["state"] = state
            context.record_activity("tcad_debug_polled")
        if state not in _TERMINAL:
            response = _pending(run_name, mode, state)
            if isinstance(details.get("progress"), dict):
                response["progress"] = details["progress"]
            return response
        _settle_wall_usage(context.state, record, (details.get("consumed_budget") or {}).get("wall_time_seconds"))
        try:
            from scidiscovery.plugin_runtime.collection import CollectionContext
            remaining = context.remaining_seconds - (time.monotonic() - call_started)
            budget = CollectionContext.for_seconds(remaining)
            budget.remaining_seconds()
            collect = getattr(self.adapter, "collect_with_budget", None)
            if self.runtime_context is not None:
                from .debug_collection import collect as collect_in_process
                collected = collect_in_process(self.runtime_context, str(record["external_run_id"]), context=budget, limits=record["collection_limits"])
            elif callable(collect):
                collected = collect(str(record["external_run_id"]), context=budget, limits=record["collection_limits"])
            else:
                raise RuntimeError("legacy debug collection requires its configured runtime factory")
            budget.remaining_seconds()
            response = self._finish(context, run_name, mode, record, collected, budget=budget)
        except TCADDebugError:
            raise
        except Exception as error:
            raise _continuation_error("tcad_debug_collection_failed",
                "The diagnostic reached a terminal state but its results could not be collected. Recover collection for this same diagnostic; do not repeat the solver.", repairable=False) from error
        record["state"] = "collected"
        record["response"] = response
        context.record_activity("tcad_debug_collected")
        return response

    def _refresh_budget_usage(self, context):
        """Settle completed jobs before deciding whether another job fits."""
        for name, record in _budget_records(context, context.state).items():
            if "actual_wall_seconds" in record or not record.get("external_run_id"):
                continue
            reader = getattr(self.adapter, "status_details", None)
            if not callable(reader):
                continue
            try:
                details = reader(record["external_run_id"])
            except Exception:
                continue  # Unknown usage stays reserved; never invent a refund.
            if details.get("state") in _TERMINAL:
                record["state"] = details["state"]
                elapsed = (details.get("consumed_budget") or {}).get("wall_time_seconds")
                if elapsed is None:
                    elapsed = (details.get("progress") or {}).get("elapsed_seconds")
                if "budget_ledgers" in context.state:
                    owner_name = next((alias for alias, location in context.state["budget_record_locations"].items()
                        if location == name), None)
                    if owner_name is None:
                        path, key = name
                        ledger = context.state["budget_ledgers"][path]
                        before = ledger.get("reserved_wall_seconds", 0)
                        _settle_wall_usage(ledger, record, elapsed)
                        context.state["reserved_wall_seconds"] += ledger.get("reserved_wall_seconds", 0) - before
                        ledger.setdefault("runs", {})[key] = record
                        continue
                    name = owner_name
                context.state.setdefault("runs", {})[name] = record
                _settle_wall_usage(context.state, record, elapsed)

    def _start(
        self, context: OperationToolContext, run_name: str, mode: str, output_names: tuple[str, ...]
    ) -> dict[str, object]:
        used = context.state.get("reserved_wall_seconds", 0)
        if type(used) is not int or used < 0:
            raise TCADDebugError("local TCAD debug budget is invalid")
        remaining = debug_budget(context)["effective_wall_seconds"]
        if remaining < 1:
            raise _budget_error(context, "tcad_debug_wall_budget_exhausted", "total_wall_seconds")
        project, source_sha, sources = _candidate(context)
        delivery = _delivery_budget(context, project, mode, output_names)
        capability = (context.state["experiment_capability"] if "experiment_capability" in context.state
            else context.read_input("execution_capability"))
        draft = DeckProjectDraft.model_validate_json(project, strict=True)
        entrypoint = draft.development_initialization_entrypoint if mode == "initialization" else draft.entrypoint
        declarations_path = context.workspace / "deck/declarations.json"
        declarations_sha = (
            canonical_sha256(json.loads(declarations_path.read_bytes()))
            if declarations_path.is_file() else None
        )
        exchange = self.exchange_root / uuid.uuid4().hex / run_name
        exchange.mkdir(parents=True, mode=0o700)
        submission_started = False
        try:
            validate_protocol = getattr(self.adapter, "validate_submission_protocol", None)
            if callable(validate_protocol):
                validate_protocol()
            prepared = self.adapter.prepare(
                project=project,
                capability=capability,
                sources=sources,
                exchange_directory=exchange,
                mode=mode,
                output_budget_bytes=delivery["effective_collection_bytes"],
                **({"output_names": output_names} if output_names else {}),
            )
            prepared = self.adapter.clamp_wall_time(prepared, wall_time_seconds=remaining)
            submission = self.adapter.prepare_submission(prepared)
            pending_record = {
                "mode": mode,
                "delivery_budget": delivery,
                "collection_limits": {"max_output_bytes": delivery["effective_collection_bytes"],
                    **{key: getattr(_debug_policy(context), key) for key in
                       ("max_output_file_bytes", "max_output_files", "max_log_bytes", "max_manifest_bytes", "max_response_log_chars")}},
                "run_id": context.run_id,
                "operation_id": context.operation_id,
                "backend_release": json.loads(capability).get("public_release_label"),
                "entrypoint": entrypoint,
                "arguments": prepared.arguments,
                "output_names": output_names,
                "collect_generated_outputs": DeckProjectDraft.model_validate_json(project).collect_generated_outputs,
                "reserved_wall_seconds": prepared.wall_time_seconds,
                "external_run_id": None,
                "state": "accepted",
                "source_tree_sha256": source_sha,
                "project_sha256": project_debug_sha256(
                    DeckProjectDraft.model_validate_json(project, strict=True)
                ),
                **({"implementation_ref": context.state["experiment_implementation_ref"]}
                    if "experiment_implementation_ref" in context.state else {}),
                "declarations_sha256": declarations_sha,
            }
            context.state["reserved_wall_seconds"] = used + prepared.wall_time_seconds
            context.state.setdefault("reservations", {})[run_name] = {
                "submission": submission.model_dump(mode="json"), "record": pending_record,
                "retirement_supported": callable(getattr(self.adapter, "retire_submission", None))}
            self._save_budget(context)
            submission_started = True
            external_run_id, state = self.adapter.submit(submission)
        except Exception as error:
            from scidiscovery.operation_contract import DiagnosticError
            if (submission_started and isinstance(error, SubmissionNotCreated)
                    and error.submission_sha256 == submission.sha256):
                rejected = {**pending_record, "state": "cancelled", "submission_absent": True,
                    "actual_wall_seconds": 0, "absence_reason": error.reason}
                context.state["runs"][run_name] = rejected
                context.state["reservations"][run_name]["record"] = rejected
                self._save_budget(context)
                message = ("The executor rejected this exact diagnostic before creating a job because capacity is full. "
                    "No solver was launched and its allowance was released. Wait for the existing work to finish, then use a new diagnostic name."
                    if error.reason == "capacity_exhausted" else
                    "This exact diagnostic was not created; its allowance was released. Repair the local capability/binding or retired identity before using a new diagnostic name.")
                raise _continuation_error("tcad_debug_not_submitted", message, repairable=True) from error
            if isinstance(error, DiagnosticError) and not submission_started:
                raise
            raise _continuation_error(
                "tcad_debug_submission_unknown" if submission_started else "tcad_debug_prepare_failed",
                "Diagnostic submission was attempted but acknowledgement is unavailable. Query status for the same name; do not create a replacement."
                if submission_started else
                "Diagnostic preparation failed before submission. No new diagnostic was sent to the executor. The control diagnostic retains the backend or contract cause; repair that cause before retrying the same implementation.",
                repairable=False) from error
        if not isinstance(external_run_id, str) or not external_run_id:
            raise TCADDebugError("TCAD debug returned an invalid private binding")
        context.state["reserved_wall_seconds"] = used + prepared.wall_time_seconds
        context.record_activity("tcad_debug_submitted")
        return {**pending_record, "external_run_id": external_run_id, "state": _state(state)}

    def _finish(self, context, run_name, mode, record, collected, *, budget=None) -> dict[str, object]:
        root = context.workspace / ".operation-tools/tcad" / run_name
        for directory in (root.parents[1], root.parent, root):
            directory.mkdir(exist_ok=True, mode=0o700)
            if directory.is_symlink() or not directory.is_dir():
                raise TCADDebugError("TCAD debug private directory is unsafe")
        outputs = []
        retained = []
        for item in collected.files:
            if budget: budget.remaining_seconds()
            if mode != "initialization" or item.name == "debug.log.txt":
                _write_private(root / item.name, item.content)
            if item.name == "debug.log.txt":
                write_control_workspace_file(
                    context.workspace,
                    Path(f"deck/reports/log-{run_name}.txt"),
                    item.content, replace=False, mode=0o400, create_parents=True,
                )
                retained.append(_file_identity(f"reports/log-{run_name}.txt", item.content, item.media_type))
            if item.name != "debug.log.txt":
                entry = {"name": item.name, "media_type": item.media_type, "size_bytes": len(item.content)}
                if item.relative_path is not None:
                    entry["source_path"] = item.relative_path
                if mode == "initialization":
                    relative = Path("deck/reports") / run_name / item.name
                    try:
                        write_control_workspace_file(context.workspace, relative, item.content,
                            replace=False, mode=0o400, create_parents=True)
                    except WorkspaceError as error:
                        raise TCADDebugError(f"initialization output path is unsafe: {relative}") from error
                    entry["relative_path"] = relative.as_posix()
                entry["sha256"] = hashlib.sha256(item.content).hexdigest()
                outputs.append(entry)
                if mode == "initialization" and (
                    item.name in record.get("output_names", ())
                    or (record.get("collect_generated_outputs") and item.name.startswith("generated_"))
                ):
                    retained.append(_file_identity(relative.relative_to("deck").as_posix(), item.content, item.media_type))
        response: dict[str, object] = {
            "run_name": run_name,
            "mode": mode,
            "state": collected.terminal_state,
            "phase": "collected",
            "development_only": True,
            "scientific_claim_admissible": False,
            "diagnostic_layer": collected.diagnostic_layer,
            "summary": collected.summary,
            "log_excerpt": collected.log_excerpt,
            "log_relative_path": (
                f"deck/reports/log-{run_name}.txt"
                if any(item.name == "debug.log.txt" for item in collected.files) else None
            ),
            "log_excerpt_is_complete": any(
                item.name == "debug.log.txt" and item.content == collected.log_excerpt.encode("utf-8")
                for item in collected.files
            ),
            "exit_code": collected.exit_code,
            "outputs": outputs,
        }
        present = {item["name"] for item in outputs if item["size_bytes"] > 0}
        missing = sorted(set(record.get("output_names", ())) - present)
        if missing:
            response["missing_outputs"] = missing
            if collected.diagnostic_layer in {"complete", "output_contract"}:
                response.update(diagnostic_layer="collection",
                    summary="Initialization diagnostic outputs missing or empty: " + ", ".join(missing))
        if collected.source_diagnostic is not None:
            response["source_diagnostic"] = asdict(collected.source_diagnostic)
        if collected.timing is not None:
            response["progress"] = collected.timing
            _settle_wall_usage(context.state, record, collected.timing.get("elapsed_seconds"))
        # Validate the complete public response before sealing a control proof.
        # Cache only the diagnostic; the handler refreshes budget on every call.
        debug_response(context, run_name, response)
        if budget: budget.remaining_seconds()
        # Preserve each attempt, including failed diagnostics, as a bounded
        # workspace record tied to the exact submitted source and declarations.
        diagnostic = {key: value for key, value in response.items() if key != "run_name"}
        diagnostic.update({key: record[key] for key in (
            "source_tree_sha256", "project_sha256", "declarations_sha256",
            "run_id", "operation_id", "backend_release", "entrypoint", "arguments", "output_names", "delivery_budget", "collection_limits"
        )})
        diagnostic["run_name"] = run_name
        if "implementation_ref" in record:
            diagnostic["implementation_ref"] = record["implementation_ref"]
        diagnostic_bytes = canonical_json(diagnostic)
        retained.append(_file_identity(f"reports/diagnostic-{run_name}.json", diagnostic_bytes, "application/json"))
        try:
            write_control_workspace_file(
                context.workspace,
                Path(f"deck/reports/diagnostic-{run_name}.json"),
                diagnostic_bytes, replace=False, mode=0o400, create_parents=True,
            )
        except WorkspaceError as error:
            raise TCADDebugError("TCAD diagnostic record path is unsafe") from error
        if mode in {"preflight", "initialization"}:
            profile = (
                "tcad.project-preflight.v1"
                if mode == "preflight"
                else "tcad.project-initialization.v1"
            )
            report_bytes = canonical_json(
                        {
                            "schema_version": 1,
                            "profile": profile,
                            "source_tree_sha256": record["source_tree_sha256"],
                            "project_sha256": record["project_sha256"],
                            "declarations_sha256": record["declarations_sha256"],
                            "mode": mode,
                            "terminal_state": collected.terminal_state,
                            "exit_code": collected.exit_code,
                            "diagnostic_layer": response["diagnostic_layer"],
                            "qualified": (
                                collected.terminal_state == "succeeded"
                                and collected.exit_code == 0
                                and response["diagnostic_layer"] == "complete"
                                and not missing
                            ),
                            "summary": response["summary"],
                        }
                    )
            try:
                write_control_workspace_file(
                    context.workspace,
                    Path(f"deck/reports/{mode}-{run_name}.json"),
                    report_bytes, replace=False, mode=0o400, create_parents=True,
                )
                write_control_workspace_file(
                    context.workspace,
                    Path(f"deck/reports/{mode}.json"),
                    report_bytes,
                    replace=True,
                    mode=0o400,
                    create_parents=True,
                )
            except WorkspaceError as error:
                raise TCADDebugError("TCAD debug report path is unsafe") from error
            retained.append(_file_identity(f"reports/{mode}-{run_name}.json", report_bytes, "application/json"))
            proof = {**diagnostic, "files": retained, "collection_complete": (
                collected.terminal_state == "succeeded" and collected.exit_code == 0
                and response["diagnostic_layer"] == "complete" and not missing)}
            context.state.setdefault("finalization_records", {})[mode] = canonical_json(proof)
        else:
            proof = {**diagnostic, "files": retained, "collection_complete": False}
        self._save_receipt(context, run_name, record, proof, response)
        return response

    @staticmethod
    def _receipt_directory(context, run_name, source_run_id):
        key = canonical_sha256({"run_id": source_run_id, "run_name": run_name})
        return Path(context.state["budget_ledger_path"]).parent / "receipts" / key

    def _save_receipt(self, context, run_name, record, proof, response):
        """Persist control-produced collection identities separately from budgets."""
        root = self._receipt_directory(context, run_name, record["run_id"])
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for item in proof["files"]:
            raw = (context.workspace / "deck" / item["relative_path"]).read_bytes()
            if len(raw) != item["size_bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise TCADDebugError("diagnostic changed before control receipt retention")
            _write_private(root / item["sha256"], raw)
        _write_private(root / "receipt.json", canonical_json({"proof": proof, "response": response,
            "submission": context.state["reservations"][run_name]["submission"]}))

    def _restore_receipt(self, context, run_name, record, *, validate=True):
        source_run = record["run_id"]
        experiment = "experiment_project" in context.state
        exact_implementation = (experiment and record.get("implementation_ref") is not None
            and record["implementation_ref"] == context.state.get("experiment_implementation_ref"))
        if not context.recovery_authorized(source_run) and not exact_implementation:
            raise TCADDebugError("cached diagnostic belongs to another Run without a control recovery relationship")
        root = self._receipt_directory(context, run_name, source_run)
        receipt = json.loads((root / "receipt.json").read_bytes())
        if receipt["submission"] != context.state["reservations"][run_name]["submission"]:
            raise TCADDebugError("retained diagnostic submission identity changed")
        proof = receipt["proof"]
        if proof["run_id"] != source_run or proof["project_sha256"] != record["project_sha256"]:
            raise TCADDebugError("retained diagnostic proof identity changed")
        if not context.recovery_authorized(source_run) and receipt["response"].get("state") not in _TERMINAL:
            raise TCADDebugError("Only a retained terminal diagnostic can be reused by another task.")
        if experiment:
            current = DeckProjectDraft.model_validate_json(context.state["experiment_project"], strict=True)
            if (record.get("implementation_ref") != context.state.get("experiment_implementation_ref")
                    or proof.get("implementation_ref") != record.get("implementation_ref")
                    or project_debug_sha256(current) != record["project_sha256"]):
                raise TCADDebugError("Retained diagnostic does not belong to this sealed implementation.")
        response = dict(receipt["response"])
        if response.get("state") not in _TERMINAL or response.get("state") != proof.get("state"):
            raise TCADDebugError("retained diagnostic terminal state changed")
        if response.get("progress") != proof.get("progress"):
            raise TCADDebugError("retained diagnostic timing changed")
        diagnostic_verified = False
        restored_files = []
        selected_report = None
        for item in proof["files"]:
            relative = Path(item["relative_path"])
            if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "reports":
                raise TCADDebugError("retained diagnostic path is unsafe")
            raw = (root / item["sha256"]).read_bytes()
            if len(raw) != item["size_bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise TCADDebugError("retained diagnostic bytes changed")
            if item["relative_path"] == f"reports/diagnostic-{run_name}.json":
                diagnostic = json.loads(raw)
                if (diagnostic != {key: value for key, value in proof.items() if key not in {"files", "collection_complete"}}
                        or any(diagnostic.get(key) != value for key, value in response.items())):
                    raise TCADDebugError("retained diagnostic response differs from its original bytes")
                diagnostic_verified = True
            # Legacy receipts retained the mutable selection path. Its original
            # bytes remain authoritative; restore them to an attempt archive.
            if item["relative_path"] == f"reports/{record['mode']}.json":
                relative = Path(f"reports/{record['mode']}-{run_name}.json")
            restored_files.append({**item, "relative_path": relative.as_posix()})
            if relative.as_posix() == f"reports/{record['mode']}-{run_name}.json":
                selected_report = raw
            target = context.workspace / "deck" / relative
            if target.exists() and target.read_bytes() != raw:
                raise TCADDebugError("current diagnostic conflicts with the retained original")
            write_control_workspace_file(context.workspace, Path("deck") / relative, raw,
                replace=True, mode=0o400, create_parents=True)
        if not diagnostic_verified:
            raise TCADDebugError("retained diagnostic original is missing")
        if selected_report is not None:
            write_control_workspace_file(context.workspace, Path(f"deck/reports/{record['mode']}.json"),
                selected_report, replace=True, mode=0o400, create_parents=True)
        restored = {**proof, "files": restored_files, "run_id": context.run_id, "operation_id": context.operation_id,
            "recovered_from_run_id": source_run,
            "original_receipt_sha256": canonical_sha256(receipt)}
        records = context.state.setdefault("finalization_records", {})
        previous = dict(records)
        if record["mode"] in {"preflight", "initialization"}:
            records[record["mode"]] = canonical_json(restored)
        if validate and not experiment:
            try:
                # Restore all completed modes before the finalizer checks the
                # current candidate. Each control receipt is independently verified.
                for other_name, other in context.state.get("runs", {}).items():
                    if (other_name != run_name and other["mode"] != record["mode"] and (self._receipt_directory(context, other_name, other["run_id"]) / "receipt.json").is_file()
                            and other.get("project_sha256") == record["project_sha256"]
                            and context.recovery_authorized(other["run_id"])):
                        self._restore_receipt(context, other_name, other, validate=False)
                context.candidate_snapshot()
                envelope = json.loads((context.output_directory / "result.json").read_bytes())
                current = DeckProjectDraft.model_validate_json(canonical_json(envelope["payload"]), strict=True)
                if project_debug_sha256(current) != record["project_sha256"]:
                    raise TCADDebugError("recovered diagnostic differs from current project")
            except Exception:
                records.clear()
                records.update(previous)
                raise
        # The receipt can survive a crash before the budget commit. Reconcile the
        # original record only after all retained identities and bytes validate.
        record["state"] = "collected"
        record["response"] = response
        _settle_wall_usage(context.state, record, response.get("progress", {}).get("elapsed_seconds"))
        context.state.setdefault("runs", {})[run_name] = record
        self._save_budget(context)
        return response


def _file_identity(path, raw, media_type):
    return {"relative_path": path, "media_type": media_type, "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}


def _delivery_budget(context, project, mode, output_names):
    """Budget encoding before reservation; a generous contract maximum is not a prediction."""
    from .project_packager import AttemptFile
    import base64
    envelope = ({"payload": json.loads(project)} if "experiment_project" in context.state
        else json.loads((context.output_directory / "result.json").read_bytes()))
    handoff = context.workspace / "deck/handoff.json"
    if handoff.is_file():
        envelope["handoff"] = json.loads(handoff.read_bytes())
    existing = {}
    for raw in context.state.get("finalization_records", {}).values():
        proof = json.loads(raw)
        if not proof.get("collection_complete") or proof["mode"] == mode or proof["project_sha256"] != project_debug_sha256(DeckProjectDraft.model_validate_json(project)):
            continue
        for item in proof["files"]:
            content = (context.workspace / "deck" / item["relative_path"]).read_bytes()
            if hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise TCADDebugError("existing selected development diagnostic was changed: " + item["relative_path"])
            try:
                text, encoding = content.decode("utf-8"), "utf8"
            except UnicodeDecodeError:
                text, encoding = base64.b64encode(content).decode("ascii"), "base64"
            existing[item["relative_path"]] = AttemptFile(relative_path=item["relative_path"], content=text, encoding=encoding).model_dump(mode="json")
    envelope["payload"]["development_diagnostics"] = list(existing.values())
    known = len(canonical_json(envelope))
    # Two reports and future attestation/envelope metadata. Binary snapshots use
    # base64; this twofold estimate is not a guarantee for arbitrary text, so the
    # final envelope check remains authoritative.
    reserve = 256 * 1024 + 1024 * len(output_names)
    remaining = DEVELOPMENT_ARTIFACT_LIMIT_BYTES - known - reserve
    if remaining <= 0:
        raise TCADDebugError(f"development delivery budget exhausted before startup: known_bytes={known}, report_reserve_bytes={reserve}, remaining_bytes={remaining}, outputs={list(output_names)}; shrink the diagnostic/source or selected output_names")
    effective = min(DeckProjectDraft.model_validate_json(project).resource_limits.max_output_bytes, _development_limits(mode, _debug_policy(context))[1], remaining // 2)
    return {"envelope_limit_bytes": DEVELOPMENT_ARTIFACT_LIMIT_BYTES, "known_encoded_bytes": known,
            "report_reserve_bytes": reserve, "remaining_encoded_bytes": remaining,
            "effective_collection_bytes": effective, "unknown_output_sizes": list(output_names),
            "final_delivery_guaranteed": False,
            "overflow_action": "retain collection error and diagnostic paths; shrink diagnostic or deliver implementation_gap; final envelope is checked again"}


def _candidate(
    context: OperationToolContext,
) -> tuple[bytes, str, tuple[TCADDebugSource, ...]]:
    try:
        if "experiment_project" in context.state:
            project = DeckProjectDraft.model_validate_json(context.state["experiment_project"], strict=True)
            sources = tuple(context.state["experiment_sources"])
        else:
            context.candidate_snapshot()
            envelope = parse_role_result(json.loads((context.output_directory / "result.json").read_bytes()))
            project = DeckProjectDraft.model_validate_json(canonical_json(envelope.payload), strict=True)
            sources = tuple(
            TCADDebugSource(
                source_name=slot.semantic_name,
                artifact_ref=context.input_ref(slot.semantic_name),
                media_type=context.input_media_type(slot.semantic_name),
                content=context.input_path(slot.semantic_name),
            )
            for slot in project.input_slots
            )
    except (RunCheckerError, RunOutputError, WorkspaceError):
        raise
    except Exception as error:
        raise TCADDebugError(
            "staged TCAD project did not pass complete output validation: "
            + str(error)[:2048]
        ) from error
    digest = hashlib.sha256()
    for item in sorted(project.files, key=lambda value: value.relative_path):
        digest.update(item.relative_path.encode() + b"\0" + item.content.encode() + b"\0")
    return canonical_json(project.model_dump(mode="json")), digest.hexdigest(), sources


def _state(value: str) -> str:
    if value not in _STATES:
        raise TCADDebugError("TCAD debug adapter state is invalid")
    return value


def _pending(run_name: str, mode: str, state: str) -> dict[str, object]:
    return {
        "run_name": run_name,
        "mode": mode,
        "state": state,
        "phase": "pending",
        "development_only": True,
        "scientific_claim_admissible": False,
    }


def _write_private(path: Path, content: bytes) -> None:
    if _RUN_NAME.fullmatch(path.name) is None:
        raise TCADDebugError("TCAD debug output name is unsafe")
    if path.is_symlink():
        raise TCADDebugError("TCAD debug result path is unsafe")
    if path.exists() and (not path.is_file() or path.read_bytes() != content):
        raise TCADDebugError("TCAD debug result changed for one run_name")
    if not path.exists():
        path.write_bytes(content)
        path.chmod(0o400)


__all__ = ["LocalTCADDebugService"]
