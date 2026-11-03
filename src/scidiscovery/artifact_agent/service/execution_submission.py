"""Durable, bounded handoff of slow submission work to the control daemon."""
from __future__ import annotations

import fcntl
import hashlib
import os
import threading
from contextlib import ExitStack, contextmanager
from contextvars import copy_context

from .engineering_diagnostics import EngineeringDiagnostics, atomic_json, diagnostic_locator, read_json
from .executions import ExecutionServiceError


class ExecutionSubmission:
    def __init__(self, bridge):
        self.bridge = bridge
        self.executions = bridge.executions

    def _directory(self, execution_id):
        self.executions.status(execution_id)  # Only existing control-owned identities.
        directory = self.executions.exchange_root / execution_id
        return directory

    @staticmethod
    def _lock(path):
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        except BaseException:
            os.close(fd)
            raise
        return fd

    def summary(self, execution_id):
        directory = self._directory(execution_id)
        try:
            value = read_json(directory / "submission.json")
        except FileNotFoundError:
            return {}
        current = self.executions.status(execution_id)
        if current.state == "abandoned":
            return {"state": "not_submitted", "resubmission_allowed": False}
        if current.external_run_id is not None:
            return {"state": "submitted", "resubmission_allowed": False}
        if value.get("state") == "pending":
            fd = self._lock(directory / "submission.lock")
            if fd is not None:
                os.close(fd)
                # A daemon restart cannot turn interrupted submission into proof
                # that the remote job never existed.
                value = {**value, "state": "unknown", "reason": "submission_owner_lost"}
        return {**{key: value[key] for key in ("state", "reason", "error") if key in value},
            "resubmission_allowed": False}

    def require_idle(self, execution_id, *, allow_unknown=False):
        if self.summary(execution_id).get("state") in ({"pending"} if allow_unknown else {"pending", "unknown"}):
            raise ExecutionServiceError("Submission is pending or uncertain. Query the same execution; do not cancel, abandon or create a replacement yet.")

    @contextmanager
    def idle(self, execution_id, *, allow_unknown=False):
        self.require_idle(execution_id, allow_unknown=allow_unknown)
        directory = self._directory(execution_id)
        directory.mkdir(parents=True, exist_ok=True)
        fd = self._lock(directory / "submission.lock")
        if fd is None:
            raise ExecutionServiceError("Submission is in progress; query the same execution before changing its lifecycle.")
        try:
            self.require_idle(execution_id, allow_unknown=allow_unknown)
            yield
        finally:
            os.close(fd)

    def start(self, *, execution_id, scope, wait_seconds, on_authorization_required, **options):
        return self._launch(execution_id, scope=scope, wait_seconds=wait_seconds,
            on_authorization_required=on_authorization_required, options=options, recover_only=False)

    def can_retire(self, execution_id):
        try:
            return read_json(self._directory(execution_id) / "submission.json").get("retirement_supported") is True
        except FileNotFoundError:
            return False

    def refresh(self, execution_id, *, scope):
        # Status recovery only looks up an exact prepared descriptor; it never
        # replays prepare/submit. None leaves the identity and reservation intact.
        if self.summary(execution_id).get("state") == "unknown":
            self._launch(execution_id, scope=scope, wait_seconds=0,
                on_authorization_required=None, options={}, recover_only=True)

    def _launch(self, execution_id, *, scope, wait_seconds, on_authorization_required, options, recover_only):
        resources = ExitStack()
        handed_off = False
        try:
            gate = getattr(self.executions, "instance_maintenance", None)
            if gate is not None:
                with gate.global_guard():
                    owner = self.executions.scheduler_bindings.find_owner(namespace="execution", object_id=execution_id)
                    lease = gate.acquire_writer(owner[0].instance_id if owner else None)
                    resources.callback(lease.close)
                    if owner:
                        self.executions.scheduler_bindings.require_active_instance(instance_id=owner[0].instance_id)
            directory = self._directory(execution_id)
            directory.mkdir(parents=True, exist_ok=True)
            fd = self._lock(directory / "submission.lock")
            if fd is None:
                return
            resources.callback(os.close, fd)
            if (self.executions.status(execution_id).external_run_id is not None
                    or self.executions.status(execution_id).state == "abandoned"):
                return
            previous = {}
            # With our lock held summary sees pending; read the durable record
            # directly to distinguish an interrupted owner from a fresh request.
            try:
                previous = read_json(directory / "submission.json")
            except FileNotFoundError:
                pass
            if previous.get("state") in {"pending", "unknown"}:
                recover_only = True
            backend = self.executions.status(execution_id).executor
            slot_key = hashlib.sha256(backend.encode()).hexdigest()
            slot = self._lock(self.executions.exchange_root / ("submission-slot-" + slot_key + ".lock"))
            if slot is None:
                if recover_only:
                    return
                raise ExecutionServiceError("Another submission to this execution backend is in progress. No new submission was accepted; query that execution before starting another.")
            resources.callback(os.close, slot)
            may_have_submitted = previous.get("may_have_submitted",
                self.executions.prepared_submission(execution_id) is not None)
            retirement_supported = previous.get("retirement_supported", not may_have_submitted and callable(
                getattr(self.bridge._adapter(self.executions.status(execution_id).executor), "retire_submission", None)))
            audit = {key: previous[key] for key in ("administrator_recovery",) if key in previous}
            def save(record):
                atomic_json(directory / "submission.json", {**record, **audit, "retirement_supported": retirement_supported,
                    "may_have_submitted": may_have_submitted})
            save({"state": "pending"})
            done = threading.Event()
            errors = []

            def before_submit():
                nonlocal may_have_submitted
                may_have_submitted = True
                save({"state": "pending"})

            def run():
                nonlocal may_have_submitted
                try:
                    if recover_only:
                        prepared = self.executions.prepared_submission(execution_id)
                        if prepared is None or not may_have_submitted:
                            result = {"state": "interrupted", "reason": "interrupted_before_remote_submission"}
                        else:
                            adapter = self.bridge._adapter(self.executions.status(execution_id).executor)
                            found = adapter.lookup_submission(prepared)
                            if found is None:
                                result = {"state": "unknown", "reason": "exact_submission_not_yet_visible"}
                            else:
                                self.bridge._record_started(execution_id, *found)
                                result = {"state": "submitted"}
                    else:
                        self.bridge.start(execution_id=execution_id, before_submit=before_submit, **options)
                        result = {"state": "submitted"}
                    save(result)
                except Exception as error:
                    from ..execution_bridge import ExecutionAuthorizationRequired
                    from ...plugin_runtime.submission import SubmissionNotCreated
                    if isinstance(error, SubmissionNotCreated) and self.executions.prepared_submission(execution_id) is None:
                        # The bridge verified the exact rejection before clearing
                        # the prepared identity and its reservation.
                        may_have_submitted = False
                    state = "unknown" if may_have_submitted else "failed"
                    if isinstance(error, ExecutionAuthorizationRequired):
                        state = "awaiting_authorization"
                        try:
                            if on_authorization_required is not None:
                                on_authorization_required()
                        except Exception as approval_error:
                            error = approval_error
                            state = "failed"
                    result = {"state": state, "may_have_submitted": may_have_submitted}
                    if state != "awaiting_authorization":
                        diagnostic = EngineeringDiagnostics(self.executions.database_path.parent.parent / "engineering-diagnostics").capture(
                            error, scope=scope, layer="execution_submission", action="recover" if recover_only else "start")
                        result["error"] = diagnostic_locator(diagnostic)
                    save(result)
                    errors.append(error)
                finally:
                    resources.close()
                    done.set()

            thread = threading.Thread(target=copy_context().run, args=(run,), daemon=True,
                name="execution-submission")
            thread.start()
            handed_off = True
            # The thread owns the maintenance lease and locks after handoff.
            if done.wait(wait_seconds) and errors and not may_have_submitted:
                raise errors[0]
            return
        finally:
            if not handed_off:
                resources.close()
