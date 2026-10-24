"""Deferred user CLI diagnostics, through the existing serial test guard.

Run independently after G3. The native case is a sandbox startup dependency
probe, not a production Worker, registered Cov, or full G1/G3 acceptance run.
Reports are local test evidence and grant no runtime authority.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
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
    return [dict(id=case, status="not_run", reason="production integration is not authored in this batch")
            for case in ("actual-worker-tool", "registered-cov", "g1-production", "g3-production")]


def case_plan(selection="all"):
    return [
        dict(id="source-evidence", case="source", lane="source", targets=list(SOURCE_NODES),
             required_checks=list(SOURCE_TESTS), selected=selection in ("all", "source"), status="not_run"),
        dict(id="sandbox-startup-dependency", case="sandbox-startup", lane="process", targets=[PROCESS_NODE],
             required_checks=list(DEPENDENCY_CHECKS), selected=selection in ("all", "sandbox-startup"), status="not_run"),
    ]


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
    return "dependency_passed" if batch.get("case") == "sandbox-startup" else "passed"


def source_report(path):
    """The exact source nodes must all run; pytest skip/empty success is insufficient."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(REPORT_LIMIT + 1)
        if len(raw) > REPORT_LIMIT:
            raise ValueError("pytest evidence exceeds byte limit")
        lines = raw.decode("utf-8").splitlines()
        completed = any(re.fullmatch(rf"{len(SOURCE_TESTS)} passed in .+", line) for line in lines)
        return dict(last_phase="source_checks_complete" if completed else "source_checks_unresolved",
                    checks={name: "passed" if completed else "not_run" for name in SOURCE_TESTS}), None
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:512]}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="static scope only; no tests, product imports or subprocesses")
    parser.add_argument("--case", choices=("all", "source", "sandbox-startup"), default="all")
    parser.add_argument("--output", type=Path, help="new repository-local result directory; relative to the repository root")
    args = parser.parse_args()
    batches = case_plan(args.case)
    print("Deferred user CLI diagnostics after G3; sandbox dependency evidence only, no production acceptance.", flush=True)
    if args.list:
        for batch in batches:
            if batch["selected"]:
                print(f"{batch['id']}: --lane {batch['lane']} {' '.join(batch['targets'])}")
        for item in coverage_not_run():
            print(f"{item['id']}: not_run ({item['reason']})")
        return 0

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
    summary = dict(status="running", scope="deferred user CLI diagnostics; dependency only",
                   repository=str(ROOT), repository_commit=None,
                   repository_commit_status="unresolved; no repository Git query is performed",
                   output=str(output), batches=batches, production_coverage=coverage_not_run())

    def save():
        summary["not_run"] = [item["id"] for item in batches if item["status"] == "not_run"]
        save_resource(output / "summary.json", summary)

    print(f"Results: {output}", flush=True)
    save()
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
                            process = subprocess.Popen(command, cwd=ROOT, stdout=log,
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
                    if batch["case"] == "source":
                        report, report_error = source_report(guard / "batch-001/pytest.log")
                    else:
                        report, report_error = read_evidence(guard / "batch-001/fixture-logs" / REPORT_NAME)
                    batch["report"] = report
                    batch["last_phase"] = report.get("last_phase") if isinstance(report, dict) else "unresolved"
                    batch["guard_cleanup"] = dict(
                        complete=isinstance(resource, dict) and resource.get("cleanup_complete") is True,
                        scope="existing test guard observed process tree")
                    batch["product_export_cleanup"] = dict(status="not_run", reason="no production attempt in this batch")
                    errors = {name: value for name, value in
                              (("result", error), ("resource", resource_error), ("report", report_error)) if value}
                    if errors:
                        batch["evidence_errors"] = errors
                    batch["status"] = classify_case(batch)
                    save()
                    print(f"[{batch['id']}] {batch['status']}; exit={batch['returncode']}; logs: {directory}", flush=True)
                if batch["status"] not in ("passed", "dependency_passed"):
                    summary["status"] = batch["status"]
                    return 1
            summary["status"] = "dependency_passed" if any(
                item["status"] == "dependency_passed" for item in batches) else "passed"
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
