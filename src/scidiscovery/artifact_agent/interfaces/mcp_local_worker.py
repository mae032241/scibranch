"""Direct stdio MCP for one trusted-local compiled Operation Run."""

from __future__ import annotations

from ...agent_execution_settings import narrative_instruction

import argparse
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ...operation_contract import DiagnosticError, validation_diagnostics, contract_diagnostic
from ...operations.tooling import parse_tool_arguments
from ...operations.spec import freeze_json, json_projection

from ...operations.catalog import CompiledCatalog, compile_installed_catalog
from ...operations.tooling import (
    WorkerToolDefinition,
    operation_local_worker_tools,
    operation_tool_contracts,
)
from ..operation_tool_context import OperationToolContext
from ..worker_services import load_operation_services
from ..runtime_plugin_bindings import (
    load_runtime_plugin_contributions,
    parse_plugin_config_assignments,
)
from ..schema.refs import ActorRef
from ..service.artifacts import ArtifactService
from ..service.local_workspace import LocalTrustedBackend, WorkspaceError
from ..service.local_workspace import workspace_input_filename, read_control_workspace_file, write_control_workspace_file
from ..service.run_outputs import RunCheckerError, RunOutputError
from ..service.runs import RunService, RunStateConflict, RunNotFound
from ..service.scheduler_bindings import SchedulerBindingService
from ..service.instance_maintenance import InstanceMaintenanceBusy, InstanceMaintenanceUnavailable
from .mcp import MCPRouter, parse_rpc_line, rpc_error
from .mcp_worker_protocol import LIFECYCLE_WORKER_TOOLS, WorkerToolError


class LocalWorkerMCPRouter:
    """Expose three lifecycle actions and this Operation's real domain tools."""

    def __init__(
        self,
        runs: RunService,
        *,
        operation_id: str,
        operation_digest: str,
        tool_services: dict[str, object] | None = None,
        run_id: str | None = None,
        trusted_caller: tuple[str, str] | None = None,
        participant: dict | None = None,
    ) -> None:
        try:
            compiled = runs.operation_catalog.operation(operation_id, digest=operation_digest)
        except KeyError as error:
            raise ValueError("local Worker operation identity is invalid") from error
        if compiled.digest != operation_digest or compiled.spec.executor.kind != "agent":
            raise ValueError("local Worker operation identity is invalid")
        self.runs = runs
        self.compiled = compiled
        self.operation_id = operation_id
        self.operation_digest = operation_digest
        self.tool_services = dict(tool_services or {})
        self.tool_services["builtin:worker.connections"] = runs.worker_connections
        self.participant = participant
        self.is_helper = participant is not None
        self._bound_run_id = run_id
        self._trusted_caller = trusted_caller
        self._previous_run_id = None
        self._tools = {tool.name: tool for tool in LIFECYCLE_WORKER_TOOLS}
        self._registered: dict[str, WorkerToolDefinition] = {}
        for tool in operation_local_worker_tools(compiled):
            self._registered[tool.name] = tool
            self._tools[tool.name] = tool
        self._register_backend_tools()
        expected = set(runs.backend.assignment_tool_names(compiled))
        if set(self._tools) != expected:
            raise ValueError("Worker router differs from its backend tool projection")
        if self.is_helper:
            self._tools = {name: tool for name, tool in self._tools.items() if not tool.owner_only}
            self._registered = {name: tool for name, tool in self._registered.items() if name in self._tools}
        self._tool_schemas = tuple(
            freeze_json(self._tools[name].schema()) for name in sorted(self._tools)
        )
        required = {
            service
            for tool in self._registered.values()
            for service in tool.required_services
        }
        self._missing_services = tuple(sorted(required - set(self.tool_services)))
        self._run_id: str | None = None
        self._opened_call_run_id: str | None = None
        self._workspace: Any | None = None
        self._completed = False
        self._tool_state: dict[str, dict[str, object]] = {}
        self._lock = threading.RLock()
        self._active_attempt = None
        self._attempt_finished = False
        self._attempt_sources = {}

    def _check_participation(self):
        if self.is_helper:
            session, thread = self._trusted_caller
            self.runs.worker_connections.participant(platform_session=session, thread_id=thread)

    def _helper_open_reply(self, status):
        """A separate projection; never overwrite the task owner's assignment/schema."""
        source = json.loads(read_control_workspace_file(self._workspace.root,
            Path("assignment.json"), max_bytes=self._workspace.assignment_path.stat().st_size))
        saved = json.loads(self.participant['request_json'])
        task = {key:saved[key] for key in ("task", "materials")}
        instruction = (
            "You are an internal helper for this scientific subtask. Investigate relevant task files, use appropriate "
            "Skills and the parent's permitted native/scientific tools, and make requested local edits or already authorized checks. "
            "Stay within the same workspace and authorization. You are not the task owner or an independent reviewer. "
            "Owner submission and stage-sealing directions in shared materials do not apply to you. Do not submit, seal stages, "
            "create approvals or delegate again. Return concise findings, reusable calculation files, verification and limitations "
            "through native completion to the responsible parent. Do not create a formal result receipt. "
            "Read the selected material navigation below; unresolved material names remain gaps. "
            "Use scid_describe for a selected tool's complete contract and retain it while unchanged and in context. "
            "Do not read the parent result schema or owner instructions unless the subtask specifically needs them."
        )
        assignment = {"participation": "helper", "name": self.participant['name'], **task,
            "role_instructions": instruction,
            "inputs": [item for item in source.get('inputs', [])
                       if item.get('source_name') in task['materials']
                       or item.get('relative_path') in task['materials']],
            "tools": [item["name"] for item in self.list_tools()]}
        relative = Path('.operation-tools/helpers') / self.participant['name'] / 'assignment.json'
        path = write_control_workspace_file(self._workspace.root, relative,
            json.dumps(assignment, ensure_ascii=False).encode(), mode=0o400, replace=True, create_parents=True)
        return {"state": "opened", "participation": "helper", "name": self.participant['name'],
            "workspace_path": str(self._workspace.root), "assignment_path": str(path),
            "instruction": "Read this task-specific assignment; select complete tool contracts with scid_describe as needed.",
            "remaining_seconds": _remaining(status.deadline_at),
            "native_usage": "unknown", "native_lifecycle": "platform_owned",
            "narrative_instruction": narrative_instruction(status.execution_profile['profile'] if status.execution_profile else None)}

    def _register_backend_tools(self) -> None:
        """Allow a backend router to add its declared transport tools."""

    def list_tools(self) -> list[dict[str, Any]]:
        return json_projection(self._tool_schemas)

    def call_tool(self, name: str, arguments: dict[str, Any] | None) -> Any:
        self._check_participation()
        self._validate_open_call(name, arguments)
        with self._maintenance_call(name):
            return self._observed_call_tool(name, arguments)

    def _validate_open_call(self, name, arguments):
        if name != "worker_open_assignment":
            return
        self._opened_call_run_id = None
        # No assignment has been selected. Invalid open arguments must not
        # enter timing, failure recording, or an old hardened transport lease.
        tool = self._tools[name]
        try:
            parse_tool_arguments(tool.input_model, arguments)
        except ValidationError as error:
            raise WorkerToolError("tool arguments do not satisfy the declared model",
                details=validation_diagnostics(error, schema=tool.schema()["inputSchema"])) from error

    def _maintenance_call(self, name, *, existing_assignment=False):
        from contextlib import contextmanager

        @contextmanager
        def guarded():
            gate = self.runs.instance_maintenance
            with gate.global_guard():
                # RunService selects and guards the new assignment on open.
                if (name == "worker_open_assignment" and not existing_assignment) or self._run_id is None:
                    yield
                else:
                    instance_id = self.runs.status(self._run_id).instance_id
                    with gate.guard(instance_id):
                        self.runs.scheduler_bindings.require_active_instance(instance_id=instance_id)
                        yield
        return guarded()

    def _check_opened_instance(self):
        if self._run_id is not None:
            instance_id = self.runs.status(self._run_id).instance_id
            self.runs.instance_maintenance.ensure_available(instance_id)
            from ..service.scheduler_bindings import SchedulerInstanceClosed
            try:
                self.runs.scheduler_bindings.require_active_instance(instance_id=instance_id)
            except SchedulerInstanceClosed as error:
                raise InstanceMaintenanceUnavailable("assignment instance is closed") from error

    def _activity_participant(self) -> dict[str, str]:
        return ({"role": "helper", "name": self.participant["name"]}
                if self.is_helper else {"role": "owner"})

    def _observed_call_tool(self, name: str, arguments: dict[str, Any] | None) -> Any:
        with self._lock:
            self._check_participation()
            started = time.monotonic()
            started_at = datetime.now(timezone.utc).isoformat()
            timing_key = uuid.uuid4().hex
            timing_enabled = False
            timing_error = None
            opening = name == "worker_open_assignment"
            timing_run_id = None if opening else self._run_id
            result = None
            self._active_attempt = None
            self._attempt_sources = {}
            self._attempt_finished = False
            try:
                tool = self._registered.get(name)
                if tool is not None and tool.record_attempts and self._run_id is not None:
                    self._active_attempt = self.runs.begin_tool_attempt(self._run_id, tool, arguments,
                        participant=self._activity_participant())
                else:
                    timing_enabled = name in self._tools
                    if timing_enabled and timing_run_id:
                        try:
                            self.runs.record_tool_observation(timing_run_id, "tool_call_started",
                                {"call_key": timing_key, "tool_name": name, "started_at": started_at,
                                 "participant": self._activity_participant()})
                        except Exception as observation_error:
                            timing_error = observation_error
                result = self._call_tool(name, arguments)
                if self._active_attempt and not self._attempt_finished:
                    raise WorkerToolError("declared tool omitted its outcome receipt")
                return result
            except (InstanceMaintenanceBusy, InstanceMaintenanceUnavailable):
                # A refused open may still have an old Agent's cached Run ID.
                # Never append timing, diagnostics or a failure to that Run.
                timing_enabled = False
                raise
            except Exception as error:
                if opening and isinstance(error, RunStateConflict) and error.run_id is not None:
                    self._run_id = error.run_id
                    self._opened_call_run_id = error.run_id
                    self._workspace = None
                    self._completed = False
                    if not self.is_helper:
                        self._tool_state.clear()
                failure = self._engineering_failure(name, error)
                engineering = failure.engineering
                if self._active_attempt and not self._attempt_finished:
                    try:
                        failure.attempt, failure.details = self._finish_attempt(rejected=True, diagnostics=failure.details)
                    except Exception as receipt_error:
                        failure = WorkerToolError("tool receipt could not be retained", details=(contract_diagnostic(
                            "attempt_recording_failed", phase="tool_execution", affected_action="tool_call",
                            message="The call has no terminal receipt; its outcome is unknown."),))
                        failure.engineering = {**engineering, "receipt_error": self._engineering_failure(name, receipt_error).engineering}
                        self._record_failure_safely(name, failure)
                        raise failure from receipt_error
                self._record_failure_safely(name, failure)
                if failure is error: raise
                raise failure from error
            finally:
                if opening:
                    timing_run_id = self._opened_call_run_id
                if timing_enabled and timing_run_id:
                    try:
                        if opening:
                            self.runs.record_tool_observation(timing_run_id, "tool_call_started",
                                {"call_key": timing_key, "tool_name": name, "started_at": started_at,
                                 "participant": self._activity_participant()})
                        self.runs.record_tool_observation(timing_run_id, "tool_call_completed",
                            {"call_key": timing_key, "tool_name": name, "started_at": started_at,
                             "completed_at": datetime.now(timezone.utc).isoformat(),
                             "duration_seconds": round(time.monotonic() - started, 6),
                             "participant": self._activity_participant(),
                             **({"helper_access": {key: result[key] for key in
                                 ("name", "access", "native_thread_state") if key in result}}
                                if name == "worker_helper" and isinstance(result, dict)
                                and "name" in result and "access" in result else {})})
                    except Exception as observation_error:
                        timing_error = observation_error
                if timing_error is not None:
                    from ..service.engineering_diagnostics import EngineeringDiagnostics
                    EngineeringDiagnostics(self.runs.database_path.parent.parent / "engineering-diagnostics").capture(
                        timing_error, scope="worker:unbound", layer="worker_timing", action=name)
                self._active_attempt = None
                self._attempt_sources = {}

    def _engineering_failure(self, name, error):
        from ..service.engineering_diagnostics import EngineeringDiagnostics
        store = EngineeringDiagnostics(self.runs.database_path.parent.parent / "engineering-diagnostics")
        scope = "worker:unbound"
        run_id = self._opened_call_run_id if name == "worker_open_assignment" else self._run_id
        try:
            if run_id:
                scope = "instance:" + self.runs.status(run_id).instance_id
        except Exception as scope_error:
            from ..service.engineering_diagnostics import exception_facts
            error.scope_lookup_error = exception_facts(scope_error, layer="worker", action="scope")
        engineering = store.capture(error, scope=scope, layer="worker", action=name)
        from ..service.engineering_diagnostics import diagnostic_locator
        if isinstance(error, DiagnosticError):
            failure = error if isinstance(error, WorkerToolError) else WorkerToolError(str(error), details=error.details)
            failure.public_engineering = {}
            if not failure.details:
                failure.details = (contract_diagnostic("tool_rejected", phase="tool_execution", affected_action="tool_call",
                    message="The experiment action could not complete with its current inputs or configured services."),)
        else:
            failure = WorkerToolError("The experiment action could not complete. Its control diagnostic has been retained.",
                details=(contract_diagnostic(engineering["category"], phase="tool_execution",
                    affected_action="tool_call", message="The configured service could not complete this action.",
                    error_type=type(error).__name__),))
            failure.public_engineering = {"category": engineering["category"], **diagnostic_locator(engineering)}
        failure.engineering = engineering
        return failure

    def _finish_attempt(self, **values):
        if self._active_attempt is None: return None
        reference, diagnostics = self.runs.finish_tool_attempt(self._run_id, self._active_attempt,
            sources=self._attempt_sources, **values)
        self._attempt_finished = True
        if not values.get("successful") and values.get("result_status") not in {None, "computed"}:
            self.runs.record_activity(self._run_id, "tool_not_computed", diagnostic={
                "category":"tool_failed", "tool_name":self._active_attempt["tool_name"],
                "details":diagnostics})
        return reference, diagnostics

    def _source_read(self, alias):
        if self._active_attempt is None or alias in self._attempt_sources: return
        descriptor = self.runs.source_descriptor(self.runs.status(self._run_id), alias)
        self._attempt_sources[alias] = {"schema_version":1,
            "artifact_ref":descriptor.artifact_ref.model_dump(mode="json"),
            "port_name":descriptor.port_name}
        self.runs.record_tool_attempt_read(self._run_id, self._active_attempt, self._attempt_sources)

    def _record_tool_failure(self, name: str, error: WorkerToolError) -> None:
        run_id = self._opened_call_run_id if name == "worker_open_assignment" else self._run_id
        if run_id is None:
            return
        self.runs.record_error_observation(run_id, "tool_failed", diagnostic={
            "category": "tool_failed", "tool_name": name,
            "details": error.details or (contract_diagnostic("tool_rejected",
                phase="tool_execution", affected_action="tool_call"),),
            "engineering": getattr(error, "engineering", None),
        })

    def _record_failure_safely(self, name: str, error: WorkerToolError) -> None:
        try:
            self._record_tool_failure(name, error)
        except Exception as recording_error:
            from ..service.engineering_diagnostics import exception_facts
            error.engineering = {**getattr(error, "engineering", {}), "activity_record_error":
                exception_facts(recording_error, layer="run_activity", action="record")}

    def _call_tool(self, name: str, arguments: dict[str, Any] | None) -> Any:
        with self._lock:
            try:
                tool = self._tools[name]
            except KeyError as error:
                raise WorkerToolError(f"unknown worker tool: {name}") from error
            try:
                parsed = parse_tool_arguments(tool.input_model, arguments)
            except ValidationError as error:
                raise WorkerToolError("tool arguments do not satisfy the declared model",
                    details=validation_diagnostics(error, schema=tool.schema()["inputSchema"])) from error
            if name == "worker_open_assignment":
                return self._open()
            if self._run_id is None or self._workspace is None:
                raise WorkerToolError("worker_open_assignment must be called first")
            if self._completed:
                if name == "worker_submit_result":
                    return self._completed_result()
                raise WorkerToolError("this Run is already completed")
            if name == "worker_heartbeat":
                status = self.runs.heartbeat(self._run_id)
                return {"state": status.state, "remaining_seconds": _remaining(status.deadline_at)}
            if name == "worker_submit_result":
                state, diagnostics = self.runs.submit(self._run_id, trusted_tool_records={
                    name: tuple(records.values())
                    for name, state in self._tool_state.items()
                    if isinstance(records := state.get("finalization_records"), dict)
                })
                self._completed = state == "completed"
                return self._completed_result() if self._completed else {
                    "state": state,
                    "diagnostics": list(diagnostics),
                }
            registered = self._registered.get(name)
            if registered is None:
                raise WorkerToolError(f"unknown local domain tool: {name}")
            try:
                if registered.handler is not None:
                    result = registered.handler(parsed)
                else:
                    assert registered.contextual_handler is not None
                    result = registered.contextual_handler(
                        parsed, self._context(name, registered)
                    )
            except RunCheckerError as error:
                status = self.runs.status(self._run_id)
                self.runs.record_failure(
                    self._run_id,
                    reason=f"compiled output checker failed: {error}",
                    expected_state="running",
                    expected_last_activity_at=status.last_activity_at,
                    category=error.category,
                    engineering=self._engineering_failure(name, error).engineering,
                )
                return {"state": "failed", "diagnostics": []}
            except (RunOutputError, WorkspaceError) as error:
                if isinstance(error, RunOutputError) and error.recorded:
                    details = error.details
                else:
                    diagnostic = self.runs.record_error_observation(self._run_id, "output_rejected",
                        diagnostic=self.runs.rejection_diagnostic(self.runs.status(self._run_id), error))
                    details = diagnostic.get("details", ())
                return {"state": "rejected", "diagnostics": list(details)}
            except DiagnosticError:
                raise
            # The common call boundary records concrete engineering failures.
            self.runs.record_activity(self._run_id, f"tool_succeeded:{name}")
            return result

    def _open(self) -> dict[str, Any]:
        if self.compiled.spec.independent_review_ports and not self.is_helper:
            if self._trusted_caller is None or self._bound_run_id is None:
                raise WorkerToolError("Formal independent review requires a trusted gateway caller and its exact assignment.")
            from ..service.worker_connections import WorkerConnections
            session, thread = self._trusted_caller
            selected = WorkerConnections(self.runs).resolve(platform_session=session, thread_id=thread)
            if selected.run_id != self._bound_run_id:
                raise WorkerToolError("This reviewer caller does not own the assigned review.")
        if self._missing_services:
            raise WorkerToolError(
                "A required experiment service is unavailable."
            )
        terminal = self._run_id is not None and self.runs.status(self._run_id).state in {"completed", "failed"}
        if self._run_id is None or self._workspace is None or terminal:
            try:
                opener = self.runs.reopen if self._bound_run_id else self.runs.open
                status, workspace = opener(
                    operation_id=self.operation_id,
                    operation_digest=self.operation_digest,
                    trusted_caller=self._trusted_caller if not self.is_helper else None,
                    **({"run_id": self._bound_run_id} if self._bound_run_id else {}),
                )
            except RunStateConflict as error:
                if error.run_id is not None:
                    raise
                if terminal and str(error) == "no exact queued Run is available":
                    self._check_opened_instance()
                    self._opened_call_run_id = self._run_id
                    return {"state": self.runs.status(self._run_id).state}
                # Read-only provenance output does not grant implicit reattachment.
                resumable_tools = any("tool_evidence" in tool.evidence_ports or tool.record_attempts
                                      for tool in self.compiled.worker_tools)
                if not resumable_tools or str(error) != "no exact queued Run is available":
                    raise WorkerToolError(str(error)) from error
                status, workspace = self.runs.reopen(operation_id=self.operation_id, operation_digest=self.operation_digest,
                    **({"run_id": self._bound_run_id} if self._bound_run_id else {}))
            self._run_id = status.run_id
            self._opened_call_run_id = status.run_id
            self._workspace = workspace
            self._completed = False
            if not self.is_helper:
                self._tool_state.clear()
        self._check_opened_instance()
        self._opened_call_run_id = self._run_id
        status = self.runs.status(self._run_id)
        if self.is_helper:
            return self._helper_open_reply(status)
        assignment = json.loads(read_control_workspace_file(self._workspace.root,
            Path("assignment.json"), max_bytes=self._workspace.assignment_path.stat().st_size))
        contracts = self._tool_contract_location(assignment)
        from ..service.worker_start import write_worker_start
        contracts['start_here_path'] = str(write_worker_start(self._workspace, assignment))
        return {
            "state": "opened",
            "narrative_instruction": narrative_instruction(
                status.execution_profile["profile"] if status.execution_profile else None),
            "workspace_path": str(self._workspace.root),
            "assignment_path": str(self._workspace.assignment_path),
            **self._input_reading_hint(),
            **self._reading_guidance(assignment),
            **contracts,
            "output_directory": str(self._workspace.output_directory),
            "domain_workspace_path": (
                None
                if self._workspace.domain_workspace_path is None
                else str(self._workspace.domain_workspace_path)
            ),
            "remaining_seconds": _remaining(status.deadline_at),
            **({"recovery_evidence": evidence} if (evidence := self.runs.recovery_evidence_status(status)) is not None else {}),
        }

    def _reading_guidance(self, assignment):
        if self._previous_run_id is None:
            return {}
        reuse, read = [], []
        old = None
        try:
            previous = self.runs.backend.open(self._previous_run_id)
            old = json.loads(read_control_workspace_file(previous.root, Path("assignment.json"),
                max_bytes=2 * 1024 * 1024))
            for key in ("role_instructions", "tool_contracts"):
                (reuse if key in old and key in assignment and old[key] == assignment[key]
                 else read).append(key)
            schema_path = Path("schema/result.schema.json")
            before = json.loads(read_control_workspace_file(previous.root, schema_path,
                max_bytes=2 * 1024 * 1024))
            after = json.loads(read_control_workspace_file(self._workspace.root, schema_path,
                max_bytes=2 * 1024 * 1024))
            (reuse if before == after else read).append("output_schema")
        except (OSError, ValueError, TypeError, WorkspaceError):
            read = [key for key in ("role_instructions", "tool_contracts", "output_schema") if key not in reuse]
        return {"reading_guidance": {"reuse_if_retained": reuse, "read": read,
            "inputs": self._input_changes(assignment, old),
            "instruction": "Same Worker continuation. Unchanged means exact bindings and uses, not remembered or previously read. Reassess this task using retained originals; do not reprint unchanged material merely to review it again. Read new, changed or unknown inputs; reread missing context or targeted details needed for verification. Always read this task, objective/input index, language, budget and output/recovery instructions. Reuse retained complete contracts listed unchanged; refresh schema after new tool evidence."}}

    def _input_changes(self, assignment, old):
        inputs = assignment.get("inputs", [])
        changes = [{"source_name": item["source_name"], "status": "unknown"} for item in inputs]
        if old is None or not isinstance(old.get("inputs"), list):
            return changes
        try:
            before = {item.source_name: item for item in self.runs.status(self._previous_run_id).inputs}
            after = {item.source_name: item for item in self.runs.status(self._run_id).inputs}
        except (KeyError, ValueError, RunStateConflict, RunNotFound):
            return changes
        if any(not isinstance(item, dict) or item.get("source_name") not in before
               for item in old["inputs"]):
            return changes
        semantics = ("media_type", "usage", "exposure", "historical")
        for item, change in zip(inputs, changes):
            current = after.get(item["source_name"])
            if current is None or any(key not in item for key in semantics):
                continue
            # Persisted bindings own identity; workspace descriptors supply presentation semantics.
            candidates = [prior for prior in old.get("inputs", [])
                          if prior.get("source_name") in before]
            equal = []
            for prior in candidates:
                binding = before[prior["source_name"]]
                if (binding.artifact_ref == current.artifact_ref
                        and all(getattr(binding, key) == getattr(current, key)
                                for key in ("port_name", "media_type", "usage", "exposure", "require_current"))
                        and all(key in prior and prior[key] == item[key] for key in semantics)
                        and all(prior.get(key) == item.get(key)
                                for key in ("source_origin", "source_provenance"))):
                    equal.append(prior)
            if len(equal) == 1:
                change["status"] = "unchanged"
                if equal[0]["source_name"] != item["source_name"]:
                    change["previous_source_name"] = equal[0]["source_name"]
            elif not equal:
                change["status"] = "changed" if any(
                    before[prior["source_name"]].port_name == current.port_name or prior.get("source_name") == item["source_name"]
                    or before[prior["source_name"]].artifact_ref == current.artifact_ref
                    for prior in candidates) else "new"
        return changes

    def _input_reading_hint(self):
        # Installed workspaces retain their frozen helper. Do not hot-rewrite it
        # or ask the Worker to compare versions or echo legacy cursors.
        try:
            helper = read_control_workspace_file(self._workspace.root,
                Path("tools/read_input.py"), max_bytes=64 * 1024)
            if b"\nREAD_INPUT_NAVIGATION = 2\n" in helper:
                return {}
            if b"\nREAD_INPUT_NAVIGATION = 1\n" in helper:
                return {"input_reading": "Older reader: repeat the same source, --file and --pointer selection with --next/--repeat/--restart; bare navigation is unavailable."}
        except (OSError, ValueError, WorkspaceError):
            pass
        return {"input_reading": "Legacy workspace: use targeted standard-library reads of assignment and input files; do not use reader offsets or hashes."}

    def _tool_contract_location(self, assignment=None):
        if assignment is None:
            assignment = json.loads(read_control_workspace_file(self._workspace.root,
                Path("assignment.json"), max_bytes=self._workspace.assignment_path.stat().st_size))
        if "tool_contracts" in assignment:
            return {"tool_contracts_path": str(self._workspace.assignment_path),
                    "tool_contracts_pointer": "/tool_contracts"}
        raw = json.dumps(self._assignment_tool_contracts(), ensure_ascii=False).encode("utf-8")
        relative = Path(".operation-tools/tool-contracts.json")
        path = write_control_workspace_file(self._workspace.root, relative, raw,
            replace=False, mode=0o400, create_parents=True)
        return {"tool_contracts_path": str(path), "tool_contracts_pointer": ""}

    def _assignment_tool_contracts(self) -> dict[str, Any]:
        # Old assignments may lack additive metadata; never replace their files
        # or use a newer contract to reopen an old Run.
        compiled = self.runs.compiled_operation(self.runs.status(self._run_id))
        assignment = json.loads(self._workspace.assignment_path.read_bytes())
        if "tool_contracts" in assignment:
            return assignment["tool_contracts"]
        return operation_tool_contracts(compiled, self.runs.backend.assignment_tool_names(compiled))

    def _completed_result(self) -> dict[str, Any]:
        assert self._run_id is not None
        receipt = self.runs.status(self._run_id).completion_receipt
        return {
            "state": "completed",
            "receipt": (
                None
                if receipt is None
                else {
                    "head_advance": receipt.head_advance,
                    "completed_at": receipt.completed_at,
                }
            ),
        }

    def _context(
        self, name: str, tool: WorkerToolDefinition
    ) -> OperationToolContext:
        assert self._run_id is not None and self._workspace is not None
        status = self.runs.status(self._run_id)
        inputs = {item.source_name: item for item in status.inputs}

        def read_input(source_name: str) -> bytes:
            try:
                if inputs[source_name].exposure == "file_reference":
                    raise ValueError("file_reference inputs require controlled input_path streaming")
                return self.runs.artifacts.read(inputs[source_name].artifact_ref)
            except KeyError as error:
                raise ValueError("tool requested an undeclared Run input") from error

        def input_binding(source_name: str):
            try:
                return inputs[source_name]
            except KeyError as error:
                raise ValueError("tool requested an undeclared Run input") from error

        def input_path(source_name: str) -> Path:
            if source_name not in {item.source_name for item in status.inputs}:
                from ...plugin_runtime.workspace import write_control_workspace_stream
                from ...agent_execution_settings import MaterialInputSettings
                descriptor = self.runs.source_descriptor(status, source_name)
                policy = MaterialInputSettings.model_validate((status.recovery_policy or {}).get("input_materials", {}))
                if self.runs.artifacts.catalog(descriptor.artifact_ref).size_bytes > policy.max_item_bytes:
                    raise ValueError("Source exceeds the configured material file budget")
                relative = Path(".operation-tools/sources") / workspace_input_filename(source_name, descriptor.media_type)
                with self.runs.artifacts.open_original(descriptor.artifact_ref) as source:
                    write_control_workspace_stream(self._workspace.root, relative,
                        iter(lambda: source.read(policy.transfer_chunk_bytes), b""))
                return self._workspace.root / relative
            item = input_binding(source_name)
            path = self._workspace.root / "inputs" / workspace_input_filename(
                source_name, item.media_type
            )
            if item.exposure == "file_reference":
                from ...plugin_runtime.workspace import write_control_workspace_stream
                from ...agent_execution_settings import MaterialInputSettings
                policy = MaterialInputSettings.model_validate((status.recovery_policy or {}).get("input_materials", {}))
                with self.runs.artifacts.open_original(item.artifact_ref) as source:
                    write_control_workspace_stream(self._workspace.root, path.relative_to(self._workspace.root),
                        iter(lambda: source.read(policy.transfer_chunk_bytes), b""))
            if not path.is_file() or path.is_symlink():
                raise ValueError("declared Run input is unavailable")
            return path

        def snapshot() -> tuple[str, ...]:
            compiled = self.compiled
            assert compiled.spec.limits is not None
            self.runs.validate_candidate(self._run_id)
            sealed = self.runs.backend.seal(
                self._run_id,
                max_files=compiled.spec.limits.max_files,
                max_bytes=compiled.spec.limits.max_output_bytes,
            )
            return tuple(item.relative_path for item in sealed.files)

        def read_evidence(alias):
            if alias in inputs:
                return read_input(alias)
            return self.runs.read_tool_evidence(status, alias)

        def execution_scope(alias):
            from ..service.executions import ExecutionService
            item = input_binding(alias)
            if not tool.evidence_ports or item.port_name != 'execution_result':
                raise ValueError('tool has no execution scope')
            return ExecutionService.resolve_result_scope(artifacts=self.runs.artifacts,
                database_path=self.runs.database_path.parent/'executions.sqlite3', result_ref=item.artifact_ref)

        from ..service.experiment_execution import scope_experiment_services

        def recovery_scope():
            origin, family = self.runs.task_family(self._run_id)
            return (origin, tuple(run.run_id for run in family), tuple((item.port_name, item.artifact_ref)
                for run in family for item in run.inputs))

        return OperationToolContext(
            services=scope_experiment_services({
                key.partition(":")[2]: self.tool_services[key]
                for key in (*tool.required_services, *tool.optional_services)
                if key in self.tool_services
            }, run_id=self._run_id),
            state=self._tool_state.setdefault(name, {}),
            run_id=self._run_id,
            operation_id=self.compiled.spec.operation_id,
            _reserve_network_request=(lambda url: self.runs.reserve_network_request(self._run_id, tool, url)) if tool.network_access else None,
            workspace=self._workspace.root,
            output_directory=self._workspace.output_directory,
            output_collections=tuple(
                item.name for item in self.compiled.spec.outputs if item.collection is not None
            ),
            remaining_seconds=_remaining(status.deadline_at),
            _io_budget=(lambda **values: self.runs.tool_io_budget(self._run_id, **values)) if tool.evidence_ports else None,
            _read_reference=(lambda request: self.runs.reference_read(self._run_id, request, tool.reference_policy)) if tool.reference_policy is not None else None,
            _read_evidence=read_evidence,
            _prior_source_bindings=lambda: self.runs.prior_source_bindings(status),
            _source_descriptor=lambda alias: self.runs.source_descriptor(status, alias),
            _source_read=self._source_read if tool.record_attempts else None,
            _finish_attempt=self._finish_attempt if tool.record_attempts else None,
            _list_evidence=lambda: self.runs.tool_evidence(self._run_id),
            _list_attempts=lambda: self.runs.tool_attempts(self._run_id),
            _execution_scope=execution_scope if tool.evidence_ports else None,
            _recovery_authorized=lambda source: self.runs.recovery_authorized(self._run_id, source),
            _recovery_scope=recovery_scope,
            _adopt_bound_evidence=(lambda **values: self.runs.adopt_bound_tool_evidence(self._run_id, allowed_ports=tool.evidence_ports, **values)) if tool.evidence_ports else None,
            _accept_evidence=(lambda **values: self.runs.accept_tool_evidence(self._run_id, tool_name=tool.name, allowed_ports=tool.evidence_ports, **values)) if tool.evidence_ports else None,
            _publish_files=(lambda request: self.runs.publish_attachments(self._run_id, request,
                workspace=self._workspace.root)) if 'attachments' in tool.evidence_ports else None,
            _input_names_for_port=lambda port: tuple(item.source_name for item in status.inputs if item.port_name == port),
            _read_input=read_input,
            _input_path=input_path,
            _input_media_type=lambda source_name: self.runs.source_descriptor(status, source_name).media_type,
            _input_ref=lambda source_name: input_binding(source_name).artifact_ref,
            _validate_outputs=lambda: self.runs.validate_candidate(self._run_id) and None,
            _record_activity=lambda activity: self.runs.record_activity(self._run_id, activity),
            _candidate_snapshot=snapshot,
        )


def build_local_worker_router(
    *,
    state_root: Path,
    operation_id: str,
    operation_digest: str,
    operation_catalog: CompiledCatalog | None = None,
    tool_services: dict[str, object] | None = None,
    local_workspace_root: Path | None = None,
    plugin_configs=None,
    approval_secret_file: Path | None = None,
    approval_base_url: str | None = None,
) -> MCPRouter:
    state = state_root.expanduser().absolute()
    catalog = operation_catalog or compile_installed_catalog()
    artifacts = ArtifactService.open(
        cas_root=state / "artifacts",
        database_path=state / "database" / "artifact_agent.sqlite3",
    )
    if local_workspace_root is None:
        raise ValueError("local workspace root is required")
    backend = LocalTrustedBackend(local_workspace_root)
    scheduler_bindings = SchedulerBindingService(
        state / "database" / "scheduler-bindings.sqlite3"
    )
    runs = RunService(
        artifacts=artifacts,
        database_path=state / "database" / "runs.sqlite3",
        service_actor=ActorRef(actor_id="root_orchestrator", actor_type="service"),
        operation_catalog=catalog,
        backend=backend,
        scheduler_bindings=scheduler_bindings,
        approval_database_path=state / "database" / "approvals.sqlite3",
    )
    services = dict(tool_services or {})
    compiled = catalog.operation(operation_id, digest=operation_digest)
    if compiled.spec.executor.capability is not None:
        from ..service.experiment_execution import open_experiment_services, bind_experiment_services
        coordinator = open_experiment_services(runs, catalog, state_root=state,
            plugin_configs=plugin_configs or {}, approval_secret_file=approval_secret_file,
            approval_base_url=approval_base_url)
        bind_experiment_services(runs, compiled, services, coordinator=coordinator)
    return MCPRouter(
        LocalWorkerMCPRouter(
            runs,
            operation_id=operation_id,
            operation_digest=operation_digest,
            tool_services=services,
        ),
        name="scidiscovery-local-operation",
    )




def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scidiscovery-local-worker-mcp")
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--operation-digest", required=True)
    parser.add_argument("--local-workspace-root", type=Path, required=True)
    parser.add_argument("--plugin-config", action="append", default=[])
    parser.add_argument("--approval-secret-file", type=Path)
    parser.add_argument("--approval-base-url")
    args = parser.parse_args(argv)
    catalog = compile_installed_catalog()
    services: dict[str, object] = {}
    if args.plugin_config:
        services.update(load_operation_services(catalog, args.operation_id,
            parse_plugin_config_assignments(tuple(args.plugin_config)),
            args.state_root.expanduser().absolute()))
    router = build_local_worker_router(
        state_root=args.state_root,
        operation_id=args.operation_id,
        operation_digest=args.operation_digest,
        operation_catalog=catalog,
        tool_services=services,
        local_workspace_root=args.local_workspace_root,
        plugin_configs=parse_plugin_config_assignments(tuple(args.plugin_config)),
        approval_secret_file=args.approval_secret_file,
        approval_base_url=args.approval_base_url,
    )
    for line in __import__("sys").stdin:
        request_id: Any = None
        try:
            request = parse_rpc_line(line)
            request_id = request.get("id") if isinstance(request, dict) else None
            response = router.handle(request)
        except Exception as error:
            response = rpc_error(request_id, error)
        if response is not None:
            print(json.dumps(response, separators=(",", ":"), sort_keys=True), flush=True)
    return 0


def _remaining(deadline_at: str) -> int:
    deadline = datetime.fromisoformat(deadline_at.removesuffix("Z") + "+00:00")
    return max(0, int((deadline - datetime.now(timezone.utc)).total_seconds()))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["LocalWorkerMCPRouter", "build_local_worker_router", "main"]
