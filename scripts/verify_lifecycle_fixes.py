"""Run the five lifecycle defect regressions through the existing serial guard.

This is a focused regression check, not the full suite or a real model Worker run.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import subprocess
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "delete": "tests/research_loop/test_instance_delete.py",
    "fork": "tests/research_loop/test_fork_consumer_recovery.py",
    "remote": "tests/operations/test_remote_runner_recovery.py",
}


def read_result(path):
    try:
        return json.loads(path.read_text()), None
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print static batch scope without importing pytest")
    parser.add_argument("--case", choices=("all", *CASES), default="all")
    parser.add_argument("--output", type=Path, help="new result directory inside the repository; relative paths use its root")
    args = parser.parse_args()
    batches = [dict(id=f"{case}-{lane}", case=case, lane=lane, target=target,
                    selected=args.case in ("all", case), status="not_run")
               for case, target in CASES.items() for lane in ("source", "process")]
    print("Five-defect lifecycle regressions only; not the full suite or real model Worker tests.", flush=True)
    if args.list:
        for batch in batches:
            if batch["selected"]:
                print(f"{batch['id']}: --lane {batch['lane']} {batch['target']}")
        return 0

    # Reuse the existing persistence and cancellation behavior only when running.
    from run_tests import SupervisorInterrupted, save_resource, termination_signals

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    requested = args.output or Path(".trial") / f"lifecycle-validation-{stamp}-{uuid.uuid4().hex[:8]}"
    output = (ROOT / requested).resolve()
    if not output.is_relative_to(ROOT):
        parser.error("--output must be inside the repository")
    try:
        output.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        parser.error(f"cannot create new output directory {output}: {exc}")
    summary = dict(status="running", repository=str(ROOT), output=str(output), batches=batches)

    def save():
        summary["not_run"] = [batch["id"] for batch in batches if batch["status"] == "not_run"]
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
                           "--lane", batch["lane"], "--output", str(guard), batch["target"]]
                batch.update(status="running", command=command, output=str(directory), returncode=None)
                save()
                print(f"[{batch['id']}] {shlex.join(command)}", flush=True)
                process = None
                interrupted = False
                try:
                    with (directory / "runner.log").open("wb") as log:
                        # Capture ownership before allowing cancellation. The runner
                        # has its own session so only it receives our forwarded signal.
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
                    interrupted = True
                    signals["interrupt"] = False
                    if process is not None:
                        try:
                            process.send_signal(exc.signum)
                        except ProcessLookupError:
                            pass
                        # Let run_tests and its guard finish their existing cleanup.
                        process.wait()
                except OSError as exc:
                    batch["error"] = f"{type(exc).__name__}: {exc}"
                finally:
                    batch["returncode"] = None if process is None else process.returncode
                    result, error = read_result(guard / "result.json")
                    resource, resource_error = read_result(guard / "batch-001/resource.json")
                    batch.update(guard_result=result, guard_resource=resource)
                    if error or resource_error:
                        batch["evidence_errors"] = dict(result=error, resource=resource_error)
                    records = result.get("batches") if isinstance(result, dict) else None
                    passed = (batch["returncode"] == 0 and isinstance(result, dict)
                        and result.get("status") == "passed" and isinstance(records, list)
                        and len(records) == 1 and isinstance(records[0], dict)
                        and records[0].get("cleanup_complete") is True
                        and records[0].get("state") == "terminal"
                        and records[0].get("returncode") == 0
                        and records[0].get("stop_reason") == "exited"
                        and isinstance(resource, dict) and resource.get("cleanup_complete") is True
                        and resource.get("state") == "terminal"
                        and resource.get("returncode") == 0 and resource.get("stop_reason") == "exited")
                    batch["status"] = "interrupted" if interrupted else "passed" if passed else "failed"
                    save()
                    print(f"[{batch['id']}] {batch['status']}; exit={batch['returncode']}; logs: {directory}", flush=True)
                if batch["status"] != "passed":
                    summary["status"] = batch["status"]
                    return 1
            summary["status"] = "passed"
            return 0
        except SupervisorInterrupted:
            summary["status"] = "interrupted"
            return 1
        except BaseException:
            summary["status"] = "failed"
            raise
        finally:
            signals["interrupt"] = False
            summary["received_signals"] = signals["received"]
            save()
            print(f"{summary['status']}: {output / 'summary.json'}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
