"""Bounded standalone-runner recovery checks; stdlib children, no solver/model."""
import io
import json
from pathlib import Path
import os
import signal
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace

import pytest

from tcad_artifact import remote_runner_py36 as runner
from tests.operations.tcad_policy_fixtures import policy_fields


def _submission_fixture(tmp_path):
    fields = policy_fields()
    tool = {"profile_id": "fixture", "solver_kind": "deterministic_tool",
            "executable": sys.executable, "arguments": [], "environment": {},
            "release_evidence": "stdlib fixture"}
    config = dict(fields, exchange_root=str(tmp_path), state_root=str(tmp_path / "state"),
                  result_root=str(tmp_path / "state" / "runs"), tools=[tool],
                  max_concurrent_runs=1, _config_path=str(tmp_path / "unused-config"))
    archive = tmp_path / "input.tar"
    raw = b"fixture input\n"
    with tarfile.open(str(archive), "w") as target:
        entry = tarfile.TarInfo("input.txt")
        entry.size = len(raw)
        target.addfile(entry, io.BytesIO(raw))
    base = {"schema_version": 4, "execution_purpose": "development_debug",
            "tool_profile": tool["profile_id"], "solver_kind": tool["solver_kind"],
            "capability_sha256": runner._capability_sha256(tool),
            "input_archive": runner._descriptor("input", str(archive), "application/x-tar"),
            "archive_entries": [{"relative_path": "input.txt", "size_bytes": len(raw),
                                 "sha256": runner._sha(raw)}],
            "arguments": [], "expected_outputs": [], "collect_generated_outputs": False,
            "limits": {"wall_time_seconds": 1, "cpu_time_seconds": 1,
                       "max_memory_bytes": 134217728, "max_output_bytes": 4096,
                       "max_storage_bytes": 131072}}
    submissions = []
    for identity in ("first", "second"):
        job = dict(base, arguments=[identity])
        path = tmp_path / (identity + ".json")
        path.write_bytes(runner._canonical(job))
        submissions.append(runner._descriptor(identity, str(path), "application/json"))
    return config, submissions


@pytest.mark.process_e2e
def test_accepted_worker_reserves_concurrency_before_running_marker(tmp_path, monkeypatch):
    """A real worker held before startup must already consume the host slot."""
    config, submissions = _submission_fixture(tmp_path)
    real_popen = subprocess.Popen
    children = []
    barrier_program = """
from pathlib import Path
import runpy, select, sys
Path(sys.argv[2], 'startup_barrier').touch()
if not select.select([sys.stdin.buffer], [], [], 5)[0]:
    raise SystemExit('startup barrier timed out')
if sys.stdin.buffer.read(1) != b'x':
    raise SystemExit('startup barrier closed')
raise SystemExit(runpy.run_path(sys.argv[1])['_run_worker']({}, sys.argv[2]))
"""

    def held_worker(arguments, **kwargs):
        run_dir = arguments[-1]
        child = real_popen([sys.executable, "-c", barrier_program, runner.__file__, run_dir],
                           **dict(kwargs, stdin=subprocess.PIPE))
        children.append((child, Path(run_dir)))
        return child

    monkeypatch.setattr(runner.subprocess, "Popen", held_worker)
    try:
        accepted = runner._submit(config, submissions[0])
        first_dir = Path(config["result_root"]) / accepted["run_id"]
        until = time.monotonic() + 3
        while not (first_dir / "startup_barrier").exists() and time.monotonic() < until:
            assert children[0][0].poll() is None
            time.sleep(0.01)
        assert (first_dir / "startup_barrier").is_file()
        assert not (first_dir / "running").exists()
        assert runner._submit(config, submissions[0])["run_id"] == accepted["run_id"]
        assert len(children) == 1, "same original submission launched twice"
        with pytest.raises(RuntimeError, match="concurrent limit"):
            runner._submit(config, submissions[1])
        assert len(children) == 1
    finally:
        # Cancel before releasing either accepted child: no configured program runs.
        for child, run_dir in children:
            runner._cancel(config, run_dir.name)
        for child, run_dir in children:
            try:
                child.communicate(b"x", timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.communicate(timeout=3)
    assert (first_dir / "done").is_file()
    assert json.loads((first_dir / "output_manifest.json").read_text())["terminal_state"] == "cancelled"
    assert not (first_dir / "solver_pid").exists()


@pytest.mark.process_e2e
def test_cancelled_worker_with_generated_fifo_still_terminalizes(tmp_path):
    """Error-path collection cannot strand a cancelled worker on a FIFO."""
    fields = policy_fields()
    work = tmp_path / "work"
    work.mkdir()
    os.mkfifo(work / "output.pipe")
    runtime = {"runner_policy": fields["runner"], "archive_entries": [],
               "executable": sys.executable, "arguments": [], "environment": {},
               "execution_purpose": "development_debug", "expected_outputs": [],
               "collect_generated_outputs": True,
               "limits": {"wall_time_seconds": 1, "cpu_time_seconds": 1,
                          "max_memory_bytes": 134217728, "max_output_bytes": 4096,
                          "max_storage_bytes": 131072}}
    (tmp_path / "runtime.json").write_text(json.dumps(runtime))
    (tmp_path / "cancel_requested").touch()
    program = "import runpy,sys; raise SystemExit(runpy.run_path(sys.argv[1])['_run_worker']({},sys.argv[2]))"
    completed = subprocess.run([sys.executable, "-c", program, runner.__file__, str(tmp_path)],
                               capture_output=True, timeout=3)
    assert completed.returncode == 130, completed.stderr
    assert (tmp_path / "done").is_file()
    assert not (tmp_path / "running").exists()
    assert not (tmp_path / "solver_pid").exists()
    manifest = json.loads((tmp_path / "output_manifest.json").read_text())
    assert manifest["terminal_state"] == "cancelled"


@pytest.mark.process_e2e
@pytest.mark.parametrize("kind", ["regular", "fifo", "replaced"])
def test_generated_fifo_rejection_has_a_regular_file_control(tmp_path, kind):
    output = tmp_path / "output"
    if kind == "fifo":
        os.mkfifo(output)
    else:
        output.write_bytes(b"output\n")
    program = """
import os, runpy, sys
runner = runpy.run_path(sys.argv[1])
if sys.argv[3] == 'replaced':
    real_open = os.open
    def replace_before_open(path, flags, *args, **kwargs):
        os.unlink(path)
        os.mkfifo(path)
        return real_open(path, flags, *args, **kwargs)
    os.open = replace_before_open
records = []
try:
    runner['_collect_generated'](sys.argv[2], [], [],
        {'max_output_bytes': 4096, 'transfer_chunk_bytes': 1024}, records)
except RuntimeError as error:
    assert sys.argv[3] != 'regular' and 'not a regular file' in str(error), str(error)
else:
    assert sys.argv[3] == 'regular' and records[0]['size_bytes'] == 7
"""
    completed = subprocess.run([sys.executable, "-c", program, runner.__file__, str(tmp_path), kind],
                               capture_output=True, timeout=2)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("facts", [(), ("runtime.json",), ("submitted_at",), ("running",)])
def test_uncertain_remote_start_keeps_host_slot_reserved(tmp_path, monkeypatch, facts):
    config, submissions = _submission_fixture(tmp_path)
    uncertain = Path(config["result_root"]) / ("run_" + "0" * 32)
    uncertain.mkdir(parents=True)
    for name in facts:
        (uncertain / name).touch()

    def unexpected_launch(*args, **kwargs):
        raise AssertionError("uncertain worker freed its host slot")

    monkeypatch.setattr(runner.subprocess, "Popen", unexpected_launch)
    with pytest.raises(RuntimeError, match="concurrent limit"):
        runner._submit(config, submissions[0])
    assert runner._lookup_submission(config, submissions[0]["sha256"]) == {"found": False}


def test_failed_remote_launch_is_not_retried_or_treated_as_free_capacity(tmp_path, monkeypatch):
    config, submissions = _submission_fixture(tmp_path)
    launches = []

    def failed_launch(*args, **kwargs):
        launches.append(args)
        raise OSError("fixture launch failure")

    monkeypatch.setattr(runner.subprocess, "Popen", failed_launch)
    with pytest.raises(OSError, match="fixture launch failure"):
        runner._submit(config, submissions[0])
    with pytest.raises(RuntimeError, match="no live worker"):
        runner._submit(config, submissions[0])
    with pytest.raises(RuntimeError, match="concurrent limit"):
        runner._submit(config, submissions[1])
    assert len(launches) == 1


def test_completed_remote_run_releases_host_slot(tmp_path, monkeypatch):
    config, submissions = _submission_fixture(tmp_path)
    completed = Path(config["result_root"]) / ("run_" + "0" * 32)
    completed.mkdir(parents=True)
    (completed / "running").touch()
    (completed / "done").touch()
    launches = []

    def reached_launch(*args, **kwargs):
        launches.append(args)
        raise OSError("fixture launch reached")

    monkeypatch.setattr(runner.subprocess, "Popen", reached_launch)
    with pytest.raises(OSError, match="fixture launch reached"):
        runner._submit(config, submissions[0])
    assert len(launches) == 1


@pytest.mark.parametrize("stage", ["generated", "diagnostic"])
@pytest.mark.parametrize("interruption", ["cancel", "deadline"])
def test_worker_recovery_and_finalization_observe_collection_stop(tmp_path, monkeypatch, stage, interruption):
    """One collection deadline/cancel hook survives prelaunch failure and finalization."""
    config, submissions = _submission_fixture(tmp_path)
    runtime = json.loads(Path(submissions[0]["local_path"]).read_text())
    runtime.update(runner_policy=config["runner"], environment={}, executable=sys.executable,
                   collect_generated_outputs=True)
    (tmp_path / "runtime.json").write_text(json.dumps(runtime))
    work = tmp_path / "work"
    work.mkdir()
    (work / "output.txt").write_bytes(b"retained output\n")
    clock = [0.0]
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def prelaunch_failure(*args):
        raise RuntimeError("fixture prelaunch failure")

    monkeypatch.setattr(runner, "_runtime_environment", prelaunch_failure)
    name = "_collect_generated" if stage == "generated" else "_augment_development_debug_log"
    original = getattr(runner, name)
    calls = []

    def interrupt(*args, **kwargs):
        calls.append(stage)
        kwargs["check"]()
        if interruption == "cancel":
            (tmp_path / "cancel_requested").touch()
        else:
            clock[0] += config["runner"]["collection_total_seconds"]
        return original(*args, **kwargs)

    monkeypatch.setattr(runner, name, interrupt)
    exit_code = runner._run_worker({}, str(tmp_path))
    assert calls == [stage]
    assert (tmp_path / "done").is_file()
    assert not (tmp_path / "running").exists()
    assert not (tmp_path / "solver_pid").exists()
    manifest = json.loads((tmp_path / "output_manifest.json").read_text())
    if interruption == "cancel":
        assert exit_code == 130 and manifest["terminal_state"] == "cancelled"
        assert "cancelled_during_collection" in manifest["error"]
    else:
        assert exit_code != 0 and manifest["terminal_state"] == "failed"
        assert "collection_time_exceeded" in manifest["error"] + ";".join(manifest["collection_errors"])
    if stage == "diagnostic":
        assert [item["relative_path"] for item in manifest["outputs"]] == ["output.txt"]
