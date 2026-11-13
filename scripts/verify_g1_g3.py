"""Deferred user CLI diagnostics, through the existing serial test guard.

Run independently after G3. Default cases never call a model. The explicit
worker-managed case calls the selected real W and its managed Local backend.
Every report is engineering evidence only, with incomplete V1-V12 coverage;
it grants no scientific qualification, runtime authority or additional budget.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
CHECK_MODULE = "tests/research_loop/check_g1_g3_cli_validation.py"
SOURCE_TESTS = (
    "test_dependency_classifier_requires_complete_evidence",
    "test_evidence_size_is_bounded",
    "test_missing_cleanup_is_incomplete",
    "test_missing_start_marker_leaves_assertions_not_run",
    "test_missing_required_checks_cannot_pass",
    "test_unreached_cases_remain_not_run",
    "test_worker_classifier_requires_actual_layers_and_all_checks",
    "test_worker_inputs_are_explicit_and_bounded",
    "test_components_and_live_scope_do_not_overclaim",
)
SOURCE_NODES = tuple(f"{CHECK_MODULE}::{name}" for name in SOURCE_TESTS)
PROCESS_NODE = f"{CHECK_MODULE}::test_sandbox_startup_dependency"
BACKEND_CHECKS = (
    "clean_export_metadata", "readonly_export", "readonly_private_git",
    "pinned_ancestors", "external_inputs", "writable_outputs",
)
DEPENDENCY_CHECKS = (
    "capture_pinned_v1", "export_bytes_v1", *BACKEND_CHECKS,
    "private_git_unchanged", "legitimate_outputs", "fixture_export_cleanup",
)
REPORT_LIMIT = 65536
STREAM_LIMIT = 8192
REPORT_NAME = "sandbox-startup.json"
WORKER_REPORT_NAME = "worker-managed.json"
WORKER_NODE = f"{CHECK_MODULE}::test_actual_worker_managed_local"
WORKER_CHECKS = (
    "worker_started", "worker_allowed_writes", "worker_git_denied",
    "backend_started", "backend_git_denied", "backend_allowed_writes",
    "version_v1_pinned", "version_v2_pinned", "uncaptured_module_absent_v1",
    "private_git_unchanged", "external_original_unchanged", "exact_code_trees",
    "same_submission_cached", "exactly_two_original_calls", "collected_entity_unchanged",
    "post_terminal_reads_preserve_controls", "native_group_stopped", "bridge_group_stopped",
    "backend_groups_stopped", "terminal_exports_removed", "frozen_settings_unchanged",
)
WORKER_INPUT_ENV = "SCID_G1_G3_WORKER_OPTIONS"
# Exact selections only: these fixtures' simulated layers are part of the scope.
COMPONENT_CASES = (
    ("git-component", "process", 11, "real host Git; same-process capture mutation, not V2 two-process acceptance", (
        "test_git_code_versions.py::test_capture_barrier_rechecks_drift_before_pinning",
        "test_git_code_versions.py::test_preflight_rejects_bounds_and_nonregular_code_before_repository_writes",
        "test_git_code_versions.py::test_transfer_preserves_exact_calls_after_source_git_is_deleted")),
    ("cleanup-component", "source", 14, "synthetic stopped calls, mocked Git/backend/replay; original resume budget controls; no production lifecycle", (
        "test_mutable_run_inputs.py::test_terminal_export_cleanup_follows_saved_projection_and_replay",
        "test_mutable_run_inputs.py::test_terminal_cleanup_retains_exports_without_exact_replay_cleanup",
        "test_execution_recovery_reliability.py::test_original_terminal_recovery_retries_exact_export_cleanup",
        "test_execution_recovery_reliability.py::test_original_terminal_recovery_cleanup_failure_keeps_fact_and_retries",
        "test_execution_recovery_reliability.py::test_original_terminal_recovery_retains_export_without_replay_stop",
        "test_execution_recovery_reliability.py::test_original_unknown_recovery_retains_export_without_repeating_submission",
        "test_execution_recovery_reliability.py::test_recorded_resume_only_cleans_saved_original_terminal_export")),
    ("fork-component", "process", 1, "real Git transfer; synthetic compiler/native facts; parent Git removed", (
        "test_fork_consumer_recovery.py::test_fork_keeps_compiler_commit_and_dirty_drafts_in_child_git",)),
    ("retirement-component", "source", 15, "six CLI/API package retirements and direct originals; no HTTP/UI runtime", (
        "test_instance_archive.py::test_archive_public_apis_are_unavailable",
        "test_instance_archive.py::test_archive_commands_reject_before_touching_originals",
        "test_instance_archive.py::test_help_has_no_archive_or_cold_package_commands",
        "test_instance_archive.py::test_sealed_node_views_and_successor_reuse_originals_without_package",
        "test_cold_checkpoint.py::test_cold_checkpoint_public_apis_are_unavailable",
        "test_cold_checkpoint.py::test_cold_checkpoint_commands_reject_before_creating_state",
        "test_cold_checkpoint.py::test_ordinary_owner_and_paused_controls_preserve_existing_budget",)),
    ("delete-component", "source", 10, "bounded synthetic disposal and IO retry; unknown retains budget; external original identity preserved", (
        "test_instance_delete.py::test_delete_removes_own_git_and_exports_without_reading_external_inputs_or_neighbors",
        "test_instance_delete.py::test_expired_io_context_leaves_original_tombstone_for_same_operation_retry",
        "test_instance_delete.py::test_unknown_or_live_originals_never_cancel_or_reset_budget")),
)
SOURCE_IDENTITY_FILES = (
    "scripts/verify_g1_g3.py", CHECK_MODULE,
    *tuple("src/scidiscovery/research_loop/" + name + ".py" for name in (
        "controller", "contracts", "native", "native_execution", "backends", "experiments",
        "local_experiments", "worker_execution_bridge", "shared_input_views", "code_versions",
        "execution", "execution_recovery", "execution_resume", "fork_service", "fork_runtime",
        "instance_archive", "instance_delete")),
)


def bounded_text(value, limit=STREAM_LIMIT):
    """Bound a report field by encoded bytes; keep the original raw log apart."""
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return dict(text=raw[:limit].decode("utf-8", errors="ignore"),
                truncated=len(raw) > limit, limit_bytes=limit)


def read_evidence(path, limit=REPORT_LIMIT):
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("evidence exceeds byte limit")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("evidence must be an object")
        return value, None
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:512]}"


def coverage_not_run():
    return [dict(id=case, status="not_run", reason=reason) for case, reason in (
        ("actual-worker-tool", "requires explicit worker-managed selection and all original operator inputs"),
        ("registered-cov", "no complete registered Cov fixture selected; argv/receipt components do not cover its namespace"),
        ("hwa-direct-originals", "no real H-to-W-to-A lifecycle selected; component reuse is partial V7 evidence"),
        ("g1-production", "partial engineering probes do not complete V1-V8"),
        ("g3-production", "component checks do not complete V9-V11 production lifecycle"),
        ("cross-filesystem", "V12 requires an explicitly available second filesystem; not exercised here"))]


def worker_options(value):
    """Operator-selected existing declarations only; no permission translation."""
    required = {"launcher", "model", "reasoning_effort", "max_tokens", "max_seconds", "native_options"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("worker-managed requires every explicit operator input")
    launcher = value["launcher"]
    if (not isinstance(launcher, list) or len(launcher) != 1 or not isinstance(launcher[0], str)
            or Path(launcher[0]).name != "codex" or "\0" in launcher[0]):
        raise ValueError("worker-managed supports one explicit codex executable, without wrapper arguments")
    for key in ("model", "reasoning_effort"):
        if not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > 128:
            raise ValueError(f"explicit {key} required")
    if type(value["max_tokens"]) is not int or not 1 <= value["max_tokens"] <= 50000:
        raise ValueError("worker-managed max_tokens must be 1..50000")
    seconds = value["max_seconds"]
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 30 <= seconds <= 180:
        raise ValueError("worker-managed max_seconds must be finite and 30..180 (existing guard stays 300s)")
    policy = value["native_options"]
    if not isinstance(policy, dict) or set(policy) != {"workspace", "network", "web", "subagents", "sandbox_workspace_write"}:
        raise ValueError("provide the exact existing native option declarations as JSON")
    if (policy["workspace"] != "workspace-write" or type(policy["network"]) is not bool
            or policy["web"] not in ("disabled", "cached", "live") or type(policy["subagents"]) is not bool):
        raise ValueError("unsupported native declaration; the probe does not downgrade permissions")
    sandbox = policy["sandbox_workspace_write"]
    if (not isinstance(sandbox, dict) or set(sandbox) != {"writable_roots", "network_access", "exclude_tmpdir_env_var", "exclude_slash_tmp"}
            or sandbox["writable_roots"] != [] or sandbox["network_access"] is not policy["network"]
            or type(sandbox["exclude_tmpdir_env_var"]) is not bool or type(sandbox["exclude_slash_tmp"]) is not bool):
        raise ValueError("unsupported or incomplete original workspace policy; extra roots are not mapped")
    return value


def source_identity():
    """User-side tracked revision plus exact relevant bytes; never scans a tree."""
    import subprocess
    import shutil
    git = shutil.which("git", path=os.defpath)
    if git is None:
        raise ValueError("host Git unavailable for source identity")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    result = subprocess.run([git, "-C", str(ROOT), "rev-parse", "--verify", "HEAD"], env=environment,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5)
    revision = result.stdout.decode("ascii", errors="replace").strip()
    if result.returncode != 0 or re.fullmatch(r"[0-9a-f]{40,64}", revision) is None:
        raise ValueError("exact source revision unavailable")
    files = {}
    for name in SOURCE_IDENTITY_FILES:
        files[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    changed = subprocess.run([git, "-C", str(ROOT), "diff", "--no-ext-diff", "--no-textconv", "--quiet", "HEAD", "--", *SOURCE_IDENTITY_FILES],
                             env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    if changed.returncode not in (0, 1):
        raise ValueError("tracked source state unavailable")
    return dict(commit=revision, selected_tracked_changes=changed.returncode == 1, files_sha256=files,
                scope="HEAD and explicit relevant file bytes only; no whole-tree or untracked inventory")


def case_plan(selection="all"):
    batches = [
        dict(id="source-evidence", case="source", lane="source", targets=list(SOURCE_NODES),
             required_checks=list(SOURCE_TESTS), expected_tests=len(SOURCE_TESTS),
             scope="dictionary report logic only", selected=selection in ("all", "source"), status="not_run"),
        dict(id="sandbox-startup-dependency", case="sandbox-startup", lane="process", targets=[PROCESS_NODE],
             required_checks=list(DEPENDENCY_CHECKS), selected=selection in ("all", "sandbox-startup"), status="not_run"),
        dict(id="worker-managed", case="worker-managed", lane="live", targets=[WORKER_NODE],
             required_checks=list(WORKER_CHECKS), scope="real model W, original bridge and Local namespace; finite engineering fixture",
             selected=selection == "worker-managed", status="not_run"),
    ]
    for name, lane, count, scope, nodes in COMPONENT_CASES:
        targets = ["tests/research_loop/" + node for node in nodes]
        batches.append(dict(id=name, case=name, lane=lane, targets=targets, expected_tests=count,
                            required_checks=targets, scope=scope, selected=selection in ("components", name), status="not_run"))
    return batches


def classify_case(batch):
    """Classify recorded facts only; zero exit or skipped checks cannot pass."""
    if batch.get("status") == "not_run":
        return "not_run"
    if batch.get("interrupted"):
        return "interrupted"
    result, resource = batch.get("guard_result"), batch.get("guard_resource")
    records = result.get("batches") if isinstance(result, dict) else None
    if (not isinstance(records, list) or len(records) != 1
            or not isinstance(records[0], dict) or not isinstance(resource, dict)):
        return "evidence_incomplete"
    record = records[0]
    if any(item.get("stop_reason") not in (None, "exited") for item in (record, resource)):
        return "guard_stopped"
    if any(item.get("state") != "terminal" or item.get("cleanup_complete") is not True
           or item.get("stop_reason") != "exited" for item in (record, resource)):
        return "evidence_incomplete"
    report = batch.get("report")
    if not isinstance(report, dict) or batch.get("evidence_errors"):
        return "evidence_incomplete"
    try:
        if len(json.dumps(report, ensure_ascii=False).encode("utf-8")) > REPORT_LIMIT:
            return "evidence_incomplete"
    except (TypeError, ValueError):
        return "evidence_incomplete"
    if report.get("evidence_errors"):
        return "evidence_incomplete"
    checks = report.get("checks")
    if not isinstance(checks, dict):
        return "evidence_incomplete"
    if batch.get("case") == "worker-managed":
        if report.get("case_id") != "worker-managed" or report.get("scope") != "real_worker_managed_local":
            return "evidence_incomplete"
        if report.get("unsupported"):
            return "unsupported"
        if "failed" in checks.values():
            return "assertion_failed"
        if report.get("worker_started") is not True or report.get("backend_started") is not True:
            return "startup_failed"
        if report.get("assertions_status") != "complete":
            return "assertion_failed" if "failed" in checks.values() else "evidence_incomplete"
    if batch.get("case") == "sandbox-startup":
        if (report.get("case_id") != "sandbox-startup-dependency"
                or report.get("scope") != "sandbox_dependency"):
            return "evidence_incomplete"
        if report.get("start_marker") is not True:
            if (report.get("assertions_status") == "not_run"
                    and all(checks.get(name) == "not_run" for name in BACKEND_CHECKS)):
                return "startup_failed"
            return "evidence_incomplete"
        cleanup = report.get("fixture_export_cleanup")
        if not isinstance(cleanup, dict) or cleanup.get("status") != "passed":
            return "evidence_incomplete"
        if report.get("native_exit") != 0:
            return "assertion_failed"
        if report.get("assertions_status") != "complete":
            return "evidence_incomplete"
    required = batch.get("required_checks")
    if not isinstance(required, list) or not required:
        return "evidence_incomplete"
    if any(checks.get(name) == "failed" for name in required):
        return "assertion_failed"
    if any(checks.get(name) != "passed" for name in required):
        return "assertion_failed" if batch.get("returncode") not in (None, 0) else "evidence_incomplete"
    if not (batch.get("returncode") == 0 and result.get("status") == "passed"
            and record.get("returncode") == 0 and resource.get("returncode") == 0):
        return "assertion_failed"
    return {"sandbox-startup": "dependency_passed", "worker-managed": "engineering_passed"}.get(batch.get("case"), "passed")


def source_report(path, required_checks=SOURCE_TESTS, expected_tests=None):
    """The exact source nodes must all run; pytest skip/empty success is insufficient."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(REPORT_LIMIT + 1)
        if len(raw) > REPORT_LIMIT:
            raise ValueError("pytest evidence exceeds byte limit")
        lines = raw.decode("utf-8").splitlines()
        count = len(required_checks) if expected_tests is None else expected_tests
        completed = any(re.fullmatch(rf"{count} passed(?:, \d+ warnings?)? in .+", line) for line in lines)
        return dict(last_phase="source_checks_complete" if completed else "source_checks_unresolved",
                    checks={name: "passed" if completed else "not_run" for name in required_checks}), None
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:512]}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="static scope only; no tests, product imports or subprocesses")
    parser.add_argument("--case", choices=("all", "source", "sandbox-startup", "components", "worker-managed",
                                           *(row[0] for row in COMPONENT_CASES)), default="all")
    parser.add_argument("--output", type=Path, help="new repository-local result directory; relative to the repository root")
    parser.add_argument("--native-launcher", help="worker-managed: original codex executable (no wrapper arguments)")
    parser.add_argument("--model", help="worker-managed: explicit original model")
    parser.add_argument("--reasoning-effort", help="worker-managed: original reasoning effort")
    parser.add_argument("--max-tokens", type=int, help="worker-managed: finite observed token target, at most 50000")
    parser.add_argument("--max-seconds", type=float, help="worker-managed: finite active seconds, 30..180")
    parser.add_argument("--native-options", help="worker-managed: exact existing native options JSON; no credentials")
    args = parser.parse_args()
    batches = case_plan(args.case)
    print("Deferred user CLI engineering checks after G3; selected evidence only, incomplete V1-V12 acceptance.", flush=True)
    if args.list:
        for batch in batches:
            if batch["selected"]:
                print(f"{batch['id']}: --lane {batch['lane']} {' '.join(batch['targets'])}")
        for item in coverage_not_run():
            print(f"{item['id']}: not_run ({item['reason']})")
        return 0

    options = None
    if args.case == "worker-managed":
        try:
            options = worker_options(dict(launcher=[args.native_launcher], model=args.model,
                reasoning_effort=args.reasoning_effort, max_tokens=args.max_tokens, max_seconds=args.max_seconds,
                native_options=json.loads(args.native_options) if args.native_options is not None else None))
        except (ValueError, TypeError) as exc:
            parser.error(f"missing/unsupported worker-managed inputs; not_run: {exc}")

    # The existing runner owns resource limits, serial locking and tree cleanup.
    import subprocess
    from run_tests import SupervisorInterrupted, save_resource, termination_signals

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    requested = args.output or Path(".trial") / f"g1-g3-cli-{stamp}-{uuid.uuid4().hex[:8]}"
    output = (ROOT / requested).resolve()
    if not output.is_relative_to(ROOT):
        parser.error("--output must be inside the repository")
    try:
        output.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        parser.error(f"cannot create new output directory {output}: {exc}")
    summary = dict(status="running", scope="selected engineering probes only; complete V1-V12 acceptance remains not_run",
                   repository=str(ROOT), source_identity=None,
                   output=str(output), batches=batches, production_coverage=coverage_not_run())

    def save():
        summary["not_run"] = [item["id"] for item in batches if item["status"] == "not_run"]
        save_resource(output / "summary.json", summary)

    print(f"Results: {output}", flush=True)
    save()
    try:
        summary["source_identity"] = source_identity()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        summary.update(status="evidence_incomplete", source_identity_error=type(exc).__name__)
        save()
        return 1
    with termination_signals() as signals:
        try:
            for batch in batches:
                if not batch["selected"]:
                    continue
                directory = output / batch["id"]
                directory.mkdir()
                guard = directory / "guard"
                command = [sys.executable, str(ROOT / "scripts/run_tests.py"),
                           "--lane", batch["lane"], "--output", str(guard), *batch["targets"]]
                batch.update(status="running", command=command, output=str(directory), returncode=None)
                save()
                print(f"[{batch['id']}] {shlex.join(command)}", flush=True)
                process = None
                try:
                    with (directory / "runner.log").open("wb") as log:
                        signals["interrupt"] = False
                        try:
                            environment = dict(os.environ)
                            environment.pop(WORKER_INPUT_ENV, None)
                            if batch["case"] == "worker-managed":
                                environment[WORKER_INPUT_ENV] = json.dumps(options)
                            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
                        finally:
                            signals["interrupt"] = True
                        if signals["received"]:
                            raise SupervisorInterrupted(signals["received"][0])
                        process.wait()
                except SupervisorInterrupted as exc:
                    batch["interrupted"] = True
                    signals["interrupt"] = False
                    if process is not None:
                        try:
                            process.send_signal(exc.signum)
                        except ProcessLookupError:
                            pass
                        process.wait()
                except OSError as exc:
                    batch["error"] = f"{type(exc).__name__}: {str(exc)[:512]}"
                finally:
                    batch["returncode"] = None if process is None else process.returncode
                    result, error = read_evidence(guard / "result.json")
                    resource, resource_error = read_evidence(guard / "batch-001/resource.json")
                    batch.update(guard_result=result, guard_resource=resource)
                    if batch["case"] != "sandbox-startup" and batch["case"] != "worker-managed":
                        report, report_error = source_report(guard / "batch-001/pytest.log",
                            batch["required_checks"], batch["expected_tests"])
                    else:
                        filename = WORKER_REPORT_NAME if batch["case"] == "worker-managed" else REPORT_NAME
                        report, report_error = read_evidence(guard / "batch-001/fixture-logs" / filename)
                    batch["report"] = report
                    batch["last_phase"] = report.get("last_phase") if isinstance(report, dict) else "unresolved"
                    batch["guard_cleanup"] = dict(
                        complete=isinstance(resource, dict) and resource.get("cleanup_complete") is True,
                        scope="existing test guard observed process tree")
                    batch["product_export_cleanup"] = (report.get("product_export_cleanup")
                        if batch["case"] == "worker-managed" and isinstance(report, dict)
                        else dict(status="not_run", reason="no production attempt in this batch"))
                    errors = {name: value for name, value in
                              (("result", error), ("resource", resource_error), ("report", report_error)) if value}
                    if errors:
                        batch["evidence_errors"] = errors
                    batch["status"] = classify_case(batch)
                    if batch["case"] == "worker-managed":
                        item = next(row for row in summary["production_coverage"] if row["id"] == "actual-worker-tool")
                        item.update(status=batch["status"], reason="selected real W/Local engineering probe only; complete V1-V12 acceptance remains unverified")
                    save()
                    print(f"[{batch['id']}] {batch['status']}; exit={batch['returncode']}; logs: {directory}", flush=True)
                if batch["status"] not in ("passed", "dependency_passed", "engineering_passed"):
                    summary["status"] = batch["status"]
                    return 1
            summary["status"] = ("engineering_passed" if any(item["status"] == "engineering_passed" for item in batches)
                else "dependency_passed" if any(item["status"] == "dependency_passed" for item in batches) else "passed")
            return 0
        except SupervisorInterrupted:
            summary["status"] = "interrupted"
            return 1
        except BaseException:
            summary["status"] = "evidence_incomplete"
            raise
        finally:
            signals["interrupt"] = False
            summary["received_signals"] = signals["received"]
            save()
            print(f"{summary['status']}: {output / 'summary.json'}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
