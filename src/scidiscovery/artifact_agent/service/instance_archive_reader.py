"""Existing P1–P3 read shapes backed only by verified archive bytes."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .instance_archive_files import hash_file
from .instance_archive_records import ArchiveError, TABLES, readonly


def _database_service(service, path):
    if service is None:
        return None
    clone = copy.copy(service)
    clone.database_path = path
    clone._connect = lambda **kwargs: readonly(path)
    return clone


def _historical_policy_record(connection, execution_id):
    """Archive-only lookup: pre-policy schemas record no policy authorization.

    A partial modern schema is not historical absence. Let the normal query
    report missing tables/columns or corruption instead of inventing a result.
    """
    from .executions import ExecutionService
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if not tables.intersection(TABLES["executions"][1:]):
        return None
    return ExecutionService._current_policy_record(connection, execution_id)


class _ReadOnly:
    """Prevent accidental service writes even to non-SQLite archive files."""
    def __init__(self, target, allowed):
        self._target, self._allowed = target, frozenset(allowed)

    def __getattr__(self, name):
        if name not in self._allowed:
            raise AttributeError("archive service exposes read operations only: " + name)
        return getattr(self._target, name)


class _ArchivedCollection:
    def __init__(self, exchange):
        self.exchange = exchange

    def summary(self, execution_id):
        from .instance_archive_files import safe_id
        from .engineering_diagnostics import read_json
        path = self.exchange / safe_id(execution_id) / "collection" / "status.json"
        if not path.exists():
            return {"state": "unavailable", "archived": True}
        return {**read_json(path), "archived": True}


def archived_model(runtime, package, manifest):
    from ..approval_ui.read_model import InstanceReadModel
    from .engineering_diagnostics import EngineeringDiagnostics
    package = Path(package)
    for name, expected in manifest["records"].items():
        actual = hash_file(package / "records" / name)
        if any(actual[key] != expected[key] for key in ("sha256", "size_bytes", "mode")):
            raise ArchiveError("archive reader database differs from registered manifest")
    artifacts = copy.copy(runtime.artifacts)
    artifacts.registry = _database_service(runtime.artifacts.registry, package / "records" / "artifacts.sqlite3")
    artifacts.cas = copy.copy(runtime.artifacts.cas)
    artifacts.cas.root = package / "objects"
    artifacts.cas.objects_root = artifacts.cas.root / "sha256"
    bindings = _database_service(runtime.scheduler_bindings, package / "records" / "scheduler.sqlite3")
    runs = _database_service(runtime.runs, package / "records" / "runs.sqlite3")
    runs.artifacts = artifacts
    runs.scheduler_bindings = bindings
    runs.scheduler_database_path = bindings.database_path
    runs.approval_database_path = package / "records" / "approvals.sqlite3"
    runs.backend = copy.copy(runtime.runs.backend)
    backend_key = "local" if runs.backend.backend_id == "local_trusted" else "hardened"
    runs.backend.root = package / "workspaces" / backend_key
    if hasattr(runs.backend, "dispatch_database"):
        runs.backend.dispatch_database = package / "records" / "dispatch.sqlite3"
        runs.backend.transport_lock_root = runs.backend.root / "transport-locks"
    approvals = _database_service(runtime.approvals, package / "records" / "approvals.sqlite3")
    if approvals is not None:
        approvals.artifacts = artifacts
    executions = _database_service(runtime.executions, package / "records" / "executions.sqlite3")
    if executions is not None:
        executions._current_policy_record = _historical_policy_record
        executions.artifacts = artifacts
        executions.approvals = approvals
        executions.exchange_root = package / "executions" / "exchange"
    # A copied Run helper must not retain its active Artifact/DB references.
    for name, value in list(vars(runs).items()):
        if name in {"artifacts", "backend"} or not hasattr(value, "__dict__"):
            continue
        if hasattr(value, "artifacts") and hasattr(value, "scheduler_database_path"):
            replacement = copy.copy(value)
            replacement.artifacts = artifacts
            replacement.scheduler_database_path = bindings.database_path
            setattr(runs, name, replacement)
    artifacts.registry = _ReadOnly(artifacts.registry, {"resolve", "get_by_id", "list_artifacts", "linked_children", "database_path"})
    artifacts.cas = _ReadOnly(artifacts.cas, {"read", "verify", "open_verified", "path_for", "root", "objects_root", "file_mode"})
    runs.backend = _ReadOnly(runs.backend, {"root", "backend_id", "backend_version", "capabilities", "open", "verify_recovery", "supports_operation"})
    artifact_reads = _ReadOnly(artifacts, {"catalog", "get_by_id", "read", "verify", "open_original", "list_artifacts", "registry", "cas"})
    binding_reads = _ReadOnly(bindings, {"database_path", "get_instance", "binding_page", "get_binding", "find_name",
        "find_owner", "instance_bindings", "scientific_selections", "selection_records", "list_instances"})
    run_reads = _ReadOnly(runs, {"status", "active_ids", "recent_ids", "diagnostic_events", "diagnostic_reference_belongs",
        "recovery_status", "tool_timing", "related_runs", "recovery_links", "backend", "database_path",
        "completed_for_output", "tool_evidence", "source_descriptor", "sealed_material_record"})
    approval_reads = _ReadOnly(approvals, {"status", "active_ids", "has_request", "review", "request_summary", "list_requests", "navigation_summary"}) if approvals else None
    execution_reads = _ReadOnly(executions, {"status", "active_ids", "record_references", "observation", "request_artifact_id", "authorization"}) if executions else None
    return InstanceReadModel(artifacts=artifact_reads, bindings=binding_reads, runs=run_reads, approvals=approval_reads,
        executions=execution_reads, operation_catalog=runtime.operation_catalog,
        engineering_diagnostics=_ReadOnly(EngineeringDiagnostics(package / "diagnostics"), {"read"}),
        execution_collection=_ArchivedCollection(package / "executions" / "exchange"))
