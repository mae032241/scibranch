"""Run one explicit pytest lane serially, with bounded memory, logs and lifetime.

Virtual address space is not capped: modern runtimes reserve large sparse ranges.
Tree RSS and system memory are sampled emergency stops, not a cgroup aggregate
hard limit or an OOM guarantee. Inherited OS limits are not raised.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import resource
import selectors
import shutil
import signal
import subprocess
import sys
import time

try:
    from scripts.compiled_worker_process_guard import _descendant_rss_kib, _poll_process, _process_identity, _signal_processes, _wait_process
except ModuleNotFoundError:  # Direct execution puts scripts/ on sys.path.
    from compiled_worker_process_guard import _descendant_rss_kib, _poll_process, _process_identity, _signal_processes, _wait_process

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "scripts/test_resources.json"
SERIAL_LOCK = Path('/tmp') / f'scid-tests-{os.getuid()}.lock'
LANES = ("source", "installed", "process", "stress", "live")


@contextmanager
def serial_lock(inherited_fd=None):
    """Share one acquired lock with a supervisor; never unlink or bypass it."""
    with (SERIAL_LOCK.open('a') if inherited_fd is None
          else os.fdopen(os.dup(inherited_fd), 'a')) as lock:
        expected, actual = SERIAL_LOCK.stat(), os.fstat(lock.fileno())
        if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
            raise ValueError('serial_lock_descriptor_mismatch')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield lock


def memory() -> dict[str, int]:
    fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    return {"MemAvailable": int(fields["MemAvailable"].split()[0]) * 1024}


class MemoryPressure:
    """Available-memory limits; global dirty pages are not a task memory limit."""

    def __init__(self, baseline, limits):
        self.baseline, self.limits = baseline, limits

    def sample(self, current):
        return [name for name, triggered in (
            ("available_below_minimum", current["MemAvailable"] < self.limits["minimum_available_bytes"]),
            ("available_drop", self.baseline["MemAvailable"] - current["MemAvailable"] > self.limits["available_drop_stop_bytes"]),
        ) if triggered]


class SupervisorInterrupted(KeyboardInterrupt):
    def __init__(self, signum):
        self.signum = signum
        super().__init__(f"received signal {signum}")


@contextmanager
def termination_signals():
    """Reusable main-thread cancellation; ignore repeated signals during cleanup."""
    state = {"received": [], "interrupt": True}
    previous = {}
    def interrupted(signum, _frame):
        state["received"].append(signum)
        if state["interrupt"]:
            raise SupervisorInterrupted(signum)
    try:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[signum] = signal.signal(signum, interrupted)
        yield state
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def save_resource(path, value):
    """Replace a complete snapshot, including on imported supervisor paths."""
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def supervision_status(path, *, now=None):
    """Read evidence only. Lost heartbeat never proves death or permits replay."""
    unknown = {"state": "unknown", "replay_safe": False}
    try:
        with Path(path).open() as stream:
            text = stream.read(65537)
        if len(text) > 65536:
            raise ValueError("oversized")
        record = json.loads(text)
        if not isinstance(record, dict):
            raise ValueError("invalid")
    except (OSError, ValueError):
        return {**unknown, "reason": "supervision_evidence_unavailable"}
    state = record.get("state")
    heartbeat = record.get("heartbeat_at_unix")
    stale_after = record.get("heartbeat_stale_after_seconds")
    if (state not in ("running", "stopping", "terminal")
            or type(heartbeat) not in (int, float) or not math.isfinite(heartbeat)
            or type(stale_after) not in (int, float) or not math.isfinite(stale_after) or stale_after <= 0):
        return {**unknown, "reason": "supervision_heartbeat_unrecorded"}
    age = (time.time() if now is None else now) - heartbeat
    result = {"state": state, "recorded_state": state, "heartbeat_age_seconds": round(age, 3),
              "stop_reason": record.get("stop_reason"), "returncode": record.get("returncode"),
              "cleanup_complete": record.get("cleanup_complete", False), "replay_safe": False}
    if state != "terminal" and (age < 0 or age > stale_after):
        result.update(state="unknown", reason="supervision_heartbeat_stale")
    return result


def run_one(command, environment, limits, output, lock_fd):
    """Persist bounded samples and final evidence, then propagate original errors.

    SIGKILL cannot run cleanup: observers must treat its stale record as unknown.
    RSS is sampled; cleanup covers identity-verified observed descendants.
    """
    started = time.monotonic()
    interval = min(1., limits["sample_interval_seconds"])
    heartbeat_interval = 1.
    result = {"state": "running", "phase": "admission", "supervisor_pid": os.getpid(),
              "child_pid": None, "returncode": None, "stop_reason": "exited",
              "cleanup_complete": False, "observed_tree_rss_high_water_bytes": 0,
              "memory_stop_triggers": [], "address_space_limit_enabled": False,
              "heartbeat_stale_after_seconds": max(5., interval * 3)}
    path = output / "resource.json"
    def save():
        result.update(heartbeat_at_unix=time.time(), elapsed_seconds=round(time.monotonic() - started, 3))
        save_resource(path, result)
    save()  # Before admission, spawn, log or selector initialization can fail.
    process = selector = log = None
    observed, written, next_heartbeat = set(), 0, 0.
    failure = None
    cleanup_errors = []
    with termination_signals() as signals:
        try:
            baseline = current = memory()
            result.update(system_memory_baseline=baseline, system_memory_final=current)
            pressure = MemoryPressure(baseline, limits)
            result["memory_stop_triggers"] = pressure.sample(current)
            if result["memory_stop_triggers"]:
                result["stop_reason"] = "system_memory_admission"
            else:
                def restrict():
                    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
                # Defer cancellation until the owned identity has been captured.
                signals["interrupt"] = False
                try:
                    process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, start_new_session=True, preexec_fn=restrict, pass_fds=(lock_fd,))
                    identity = _process_identity(process.pid)
                    if identity is not None:
                        observed.add(identity)
                finally:
                    signals["interrupt"] = True
                if signals["received"]:
                    raise SupervisorInterrupted(signals["received"][0])
                result.update(child_pid=process.pid, phase="sampling")
                save()
                selector = selectors.DefaultSelector()
                selector.register(process.stdout, selectors.EVENT_READ)
                os.set_blocking(process.stdout.fileno(), False)
                log = (output / "pytest.log").open("wb", buffering=0)
                def drain(timeout):
                    nonlocal written
                    for key, _ in selector.select(timeout):
                        while True:
                            try:
                                chunk = os.read(key.fd, 65536)
                            except BlockingIOError:
                                break
                            if not chunk:
                                selector.unregister(key.fileobj)
                                break
                            log.write(chunk[:max(0, limits["log_bytes"] - written)])
                            written += len(chunk)
                            if written > limits["log_bytes"]:
                                result["stop_reason"] = "log_limit"
                                break
                while True:
                    stop_sample = {}
                    descendants, rss = _descendant_rss_kib(observed, diagnostic=stop_sample)
                    observed.update(descendants)
                    result["observed_tree_rss_high_water_bytes"] = max(result["observed_tree_rss_high_water_bytes"], rss * 1024)
                    current = memory()
                    result["system_memory_final"] = current
                    result["memory_stop_triggers"] = pressure.sample(current)
                    if time.monotonic() >= next_heartbeat:
                        save()
                        next_heartbeat = time.monotonic() + heartbeat_interval
                    if result["memory_stop_triggers"]:
                        result["memory_stop_sample"] = stop_sample
                        result["stop_reason"] = "system_memory_stop"
                        break
                    if result["observed_tree_rss_high_water_bytes"] > limits["observed_tree_rss_stop_bytes"]:
                        result["memory_stop_sample"] = stop_sample
                        result["stop_reason"] = "observed_tree_rss_limit"
                        break
                    if time.monotonic() - started >= limits["timeout_seconds"]:
                        result["stop_reason"] = "timeout"
                        break
                    fixture_bytes = sum(p.stat().st_size for p in (output / "fixture-logs").glob("*.log"))
                    if fixture_bytes > limits["log_bytes"]:
                        result["stop_reason"] = "fixture_log_limit"
                        break
                    drain(interval)
                    if result["stop_reason"] != "exited":
                        break
                    if _poll_process(process, observed) is not None:
                        # Drain bytes written between select and poll. Descendants
                        # holding the pipe open do not delay owned-tree cleanup.
                        drain(0)
                        break
        except BaseException as exc:
            failure = exc
            result["exception"] = {"type": type(exc).__name__, "message": str(exc)[:1024]}
            if result["stop_reason"] == "exited":
                result["stop_reason"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "supervisor_error"
            raise
        finally:
            signals["interrupt"] = False
            result.update(state="stopping", phase="cleanup", received_signals=signals["received"])
            # Each cleanup step is independent; none may replace the first fault.
            def cleanup(step):
                try:
                    step()
                except BaseException as exc:
                    cleanup_errors.append(exc)
            cleanup(save)
            if process is not None:
                def wait_child():
                    deadline = time.monotonic() + limits["termination_grace_seconds"]
                    while True:
                        try:
                            return _wait_process(process, observed,
                                timeout=min(heartbeat_interval, max(0., deadline - time.monotonic())))
                        except subprocess.TimeoutExpired:
                            cleanup(save)
                            if time.monotonic() >= deadline:
                                raise
                def observe():
                    descendants, _ = _descendant_rss_kib(observed)
                    observed.update(descendants)
                cleanup(observe)
                cleanup(lambda: _signal_processes(observed, signal.SIGTERM))
                try:
                    wait_child()
                except subprocess.TimeoutExpired:
                    pass
                except BaseException as exc:
                    cleanup_errors.append(exc)
                cleanup(observe)
                cleanup(lambda: _signal_processes(observed, signal.SIGKILL))
                cleanup(wait_child)
                result["returncode"] = process.returncode
                cleanup(process.stdout.close)
            if selector is not None:
                cleanup(selector.close)
            if log is not None:
                cleanup(log.close)
            result.update(state="terminal", phase="finished", cleanup_complete=not cleanup_errors,
                          received_signals=signals["received"])
            if cleanup_errors:
                result["cleanup_errors"] = [{"type": type(e).__name__, "message": str(e)[:1024]} for e in cleanup_errors]
                if failure is None and result["stop_reason"] == "exited":
                    result["stop_reason"] = "cleanup_error"
            cleanup(save)
            if cleanup_errors:
                if failure is not None:
                    failure.add_note("supervisor cleanup/persistence errors: " + ", ".join(type(e).__name__ for e in cleanup_errors))
                else:
                    raise cleanup_errors[0]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=LANES, default="source")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, required=True, help="new persistent result directory outside /tmp")
    parser.add_argument("--installation-cohort", choices=("core", "tcad"), default="core")
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--per-file", action="store_true", help="fresh pytest process per test file; stops on failure")
    parser.add_argument("targets", nargs="*", help="pytest files or node IDs; defaults to tests")
    args = parser.parse_args()
    defaults = json.loads(DEFAULT_CONFIG.read_text())
    supplied = json.loads(args.config.read_text())
    supplied_limits = dict(supplied.get("limits", {}))
    if "address_space_bytes" in supplied_limits:
        supplied_limits.pop("address_space_bytes")
        print("address_space_bytes is obsolete and ignored; virtual address space is not capped", file=sys.stderr)
    config = {**defaults, **supplied, "limits": {**defaults["limits"], **supplied_limits}}
    limits = config["limits"]
    required = defaults["limits"].keys()
    if set(limits) != set(required) or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
                                         for v in limits.values()):
        parser.error("resource config must contain exactly the documented positive limits")
    if not isinstance(limits["log_bytes"], int):
        parser.error("log_bytes must be an integer")
    if config["numeric_threads"] != 1:
        parser.error("numeric_threads must be 1; concurrent test workers are unsupported")
    if args.per_file and args.lane == "installed":
        parser.error("installation smoke uses one environment once; per-file installation is forbidden")
    targets = args.targets or (["tests/installation"] if args.lane == "installed" else ["tests"])
    installation = ROOT / "tests" / "installation"
    for target in targets:
        path = (ROOT / target.split("::", 1)[0]).resolve()
        if not path.is_relative_to(ROOT / "tests") or not path.exists():
            parser.error(f"target must exist under tests/: {target}")
        if args.lane == "installed" and not path.is_relative_to(installation):
            parser.error("installed lane accepts only tests/installation targets")
        if args.lane != "installed" and path.is_relative_to(installation):
            parser.error("functional lanes cannot target installation checks")
    batches = [targets]
    if args.per_file:
        expanded = []
        for target in targets:
            path = ROOT / target.split("::", 1)[0]
            if path.is_dir():
                expanded.extend(str(p.relative_to(ROOT)) for p in sorted(path.rglob("test_*.py"))
                                if not p.is_relative_to(installation))
            else:
                expanded.append(target)
        batches = [[target] for target in dict.fromkeys(expanded)]
    with ExitStack() as stack:
        try:
            inherited = os.environ.get("SCID_TEST_LOCK_FD")
            lock = stack.enter_context(serial_lock(None if inherited is None else int(inherited)))
        except BlockingIOError:
            parser.error("another pytest/runner owns this repository's serial lock")
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=False)
        environment = dict(os.environ)
        for key in ("PYTHONPATH", "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "SCIDISCOVERY_ROLE_DIR"):
            environment.pop(key, None)
        environment.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
            MPLBACKEND="Agg", SCID_TEST_LOCK_FD=str(lock.fileno()), SCID_TEST_GUARD="1")
        for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"):
            environment[key] = "1"
        stack.enter_context(termination_signals())
        results = {"lane": args.lane, "config": config, "status": "running", "batches": []}
        def save():
            save_resource(output / "result.json", results)
        save()
        try:
            for index, batch in enumerate(batches, 1):
                batch_output = output / f"batch-{index:03}"
                batch_output.mkdir()
                scratch = batch_output / "scratch"
                scratch.mkdir()
                environment.update(TMPDIR=str(scratch), SCID_TEST_LOG_DIR=str(batch_output / "fixture-logs"),
                    SCID_TEST_LOG_BYTES=str(limits["log_bytes"]), MPLCONFIGDIR=str(batch_output / "matplotlib"))
                command = [sys.executable, "-B", "-m", "pytest", "-q", "--tb=short", "--strict-markers", "-o", "addopts=",
                    "-p", "no:cacheprovider", "--test-lane", args.lane, "--basetemp", str(batch_output / "tmp")]
                if args.lane == "installed":
                    command.extend(["--installation-cohort", args.installation_cohort])
                if args.collect_only:
                    command.append("--collect-only")
                command.extend(batch)
                print(f"[{index}/{len(batches)}] {args.lane}: {' '.join(batch)}", flush=True)
                try:
                    result = run_one(command, environment, limits, batch_output, lock.fileno())
                finally:
                    shutil.rmtree(batch_output / "tmp", ignore_errors=True)
                    shutil.rmtree(scratch, ignore_errors=True)
                results["batches"].append({"targets": batch, **result})
                # Per-file selection can legitimately find only a different lane.
                passed = result["stop_reason"] == "exited" and (result["returncode"] == 0 or
                    (args.per_file and result["returncode"] == 5))
                results["status"] = "running" if passed else "failed"
                save()
                print(json.dumps(result), flush=True)
                if not passed:
                    return 1
            results["status"] = "passed"
            save()
        except BaseException as exc:
            results["status"] = "interrupted"
            try:
                save()
            except BaseException as error:
                exc.add_note("runner final persistence error: " + type(error).__name__)
            raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
