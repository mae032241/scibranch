"""Bounded standalone-runner recovery checks; stdlib children, no solver/model."""
import io
import json
from pathlib import Path
import os
import select
import signal
import subprocess
import sys
import tarfile
import threading
import time
import uuid
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
    base = {"schema_version": 5, "execution_purpose": "development_debug",
            "tool_profile": tool["profile_id"], "solver_kind": tool["solver_kind"],
            "capability_sha256": runner._capability_sha256(tool),
            "input_archive": runner._descriptor("input", str(archive), "application/x-tar"),
            "archive_entries": [{"relative_path": "input.txt", "size_bytes": len(raw)}],
            "arguments": [], "expected_outputs": [], "collect_generated_outputs": False,
            "limits": {"wall_time_seconds": 1, "cpu_time_seconds": 1,
                       "max_memory_bytes": 134217728, "max_output_bytes": 4096,
                       "max_storage_bytes": 131072}}
    submissions = []
    for identity in ("first", "second"):
        job = dict(base, request_id=uuid.uuid4().hex, arguments=[identity])
        path = tmp_path / (identity + ".json")
        path.write_bytes(runner._canonical(job))
        submissions.append(runner._descriptor(identity, str(path), "application/json"))
    return config, submissions


def _launchable_run_fixture(tmp_path, monkeypatch):
    config, submissions = _submission_fixture(tmp_path)
    job = json.loads(Path(submissions[0]["local_path"]).read_bytes())
    job["arguments"] = ["-c", "with open('executions', 'a') as out: out.write('run\\n')"]
    path = tmp_path / "launchable.json"
    path.write_bytes(runner._canonical(job))
    descriptor = runner._descriptor("launchable", str(path), "application/json")
    # Prepare the actual accepted original without launching until the barrier.
    with monkeypatch.context() as patch:
        patch.setattr(runner.subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=os.getpid()))
        accepted = runner._submit(config, descriptor)
    return config, descriptor, accepted["run_id"], Path(config["result_root"]) / accepted["run_id"]


_CONTENDED_WORKER_PROGRAM = r'''
import fcntl, json, runpy, sys
r = runpy.run_path(sys.argv[1])
real_flock = fcntl.flock
def observed_flock(fd, operation):
    # Prove that the other process really owns this lease before reporting
    # startup's requested mode. No scheduling delay can turn this into no race.
    try:
        real_flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        pass
    else:
        raise AssertionError('worker did not encounter the held lease')
    print(json.dumps({'contended': True, 'blocking': not bool(operation & fcntl.LOCK_NB)}), flush=True)
    return real_flock(fd, operation)
fcntl.flock = observed_flock
raise SystemExit(r['_run_worker']({}, sys.argv[2]))
'''


def _read_process_barrier(child):
    assert select.select([child.stdout], [], [], 3)[0], "child did not reach its barrier"
    line = child.stdout.readline()
    assert line, "child exited before its barrier"
    return json.loads(line)


@pytest.mark.process_e2e
def test_query_lease_before_worker_start_waits_then_completes_original(tmp_path, monkeypatch):
    """The real reconciliation lease must delay startup without losing it."""
    config, descriptor, run_id, root = _launchable_run_fixture(tmp_path, monkeypatch)
    real_read = runner._read_regular
    children = []

    def held_query(path, *args, **kwargs):
        if path == str(root / "worker.json") and not children:
            # _reconcile_worker calls this only after acquiring worker.lock.
            child = subprocess.Popen(
                [sys.executable, "-c", _CONTENDED_WORKER_PROGRAM, runner.__file__, str(root)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            children.append(child)
            assert _read_process_barrier(child) == {"contended": True, "blocking": True}
            assert child.poll() is None
            assert not (root / "worker.json").exists()
            assert not (root / "pid").exists()
            assert not (root / "work" / "executions").exists()
        return real_read(path, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(runner, "_read_regular", held_query)
            assert runner._status(config, run_id)["run_id"] == run_id
        stdout, stderr = children[0].communicate(timeout=5)
        assert children[0].returncode == 0, (stdout, stderr)
        assert (root / "work" / "executions").read_text() == "run\n"
        worker_raw = (root / "worker.json").read_bytes()
        manifest_raw = (root / "output_manifest.json").read_bytes()
        assert runner._lookup_submission(config, run_id[4:])["state"] == "succeeded"
        assert runner._collect(config, run_id)["run_id"] == run_id
        assert runner._submit(config, descriptor)["run_id"] == run_id
        assert (root / "worker.json").read_bytes() == worker_raw
        assert (root / "output_manifest.json").read_bytes() == manifest_raw
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=3)


@pytest.mark.process_e2e
def test_duplicate_worker_waits_without_reexecuting_original(tmp_path, monkeypatch):
    """A queued second worker must not overwrite the first worker's identity."""
    config, descriptor, run_id, root = _launchable_run_fixture(tmp_path, monkeypatch)
    held_worker_program = r'''
import json, runpy, select, sys
r = runpy.run_path(sys.argv[1])
owned = r['_run_worker_owned']
def held(config, directory, worker):
    print(json.dumps({'owned': True}), flush=True)
    if not select.select([sys.stdin.buffer], [], [], 5)[0] or sys.stdin.buffer.read(1) != b'x':
        raise AssertionError('worker barrier closed or timed out')
    return owned(config, directory, worker)
r['_run_worker'].__globals__['_run_worker_owned'] = held
raise SystemExit(r['_run_worker']({}, sys.argv[2]))
'''
    children = []
    try:
        first = subprocess.Popen([sys.executable, "-c", held_worker_program, runner.__file__, str(root)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        children.append(first)
        assert _read_process_barrier(first) == {"owned": True}
        original_identity = json.loads((root / "worker.json").read_bytes())["worker"]
        second = subprocess.Popen([sys.executable, "-c", _CONTENDED_WORKER_PROGRAM, runner.__file__, str(root)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        children.append(second)
        assert _read_process_barrier(second) == {"contended": True, "blocking": True}
        stdout, stderr = first.communicate(b"x", timeout=5)
        assert first.returncode == 0, (stdout, stderr)
        stdout, stderr = second.communicate(timeout=5)
        assert second.returncode != 0
        assert b"file already exists" in stderr
        assert (root / "work" / "executions").read_text() == "run\n"
        assert json.loads((root / "worker.json").read_bytes())["worker"] == original_identity
        assert runner._submit(config, descriptor)["run_id"] == run_id
        assert runner._status(config, run_id)["state"] == "succeeded"
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=3)


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
    assert runner._lookup_submission(config, json.loads(Path(submissions[0]["local_path"]).read_bytes())["request_id"]) == {"found": False}


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


def _stopped_worker_fixture(tmp_path, *, rebooted=False):
    config, submissions = _submission_fixture(tmp_path)
    job = json.loads(Path(submissions[0]['local_path']).read_bytes())
    run_id = 'run_' + job['request_id']
    root = Path(config['result_root']) / run_id
    (root / 'work').mkdir(parents=True)
    (root / 'submission.json').write_bytes(runner._canonical(job))
    (root / 'submitted_at').write_text('2026-10-28T00:00:00Z')
    (root / 'started_at').write_text('2026-10-28T00:00:01Z')
    (root / 'running').touch()
    identity = runner._process_identity(os.getpid())
    if rebooted:
        identity['boot_id'] = 'previous-boot'
    else:
        # Positive PID-reuse evidence, even though that numeric PID is alive.
        identity['start_time'] = str(int(identity['start_time']) + 1)
    (root / 'worker.json').write_text(json.dumps(dict(worker=identity, solver=None)))
    return config, submissions, run_id, root


@pytest.mark.parametrize('rebooted', [False, True])
@pytest.mark.parametrize('cancelled', [False, True])
def test_status_reconciles_exact_stopped_worker_and_releases_slot(tmp_path, monkeypatch, rebooted, cancelled):
    config, submissions, run_id, root = _stopped_worker_fixture(tmp_path, rebooted=rebooted)
    if rebooted:
        worker = json.loads((root / 'worker.json').read_bytes())
        worker['solver'] = dict(worker['worker'])
        (root / 'worker.json').write_text(json.dumps(worker))
    if cancelled:
        (root / 'cancel_requested').touch()
    state = runner._lookup_submission(config, run_id[4:])
    assert state['found'] is True and state['run_id'] == run_id
    assert state['state'] == ('cancelled' if cancelled else 'failed')
    assert state['done'] is True and state['consumed_budget'] is not None
    assert runner._retire_submission(config, run_id[4:])['found'] is True
    collected = runner._collect(config, run_id)
    assert {item['name'] for item in collected['outputs']} == {'tcad_log', 'tcad_manifest'}
    assert json.loads((root / 'output_manifest.json').read_bytes())['error'] == 'remote_worker_lost'
    assert not (root / 'running').exists()
    launches = []
    def next_launch(*args, **kwargs):
        launches.append(args)
        raise OSError('next original launch reached')
    monkeypatch.setattr(runner.subprocess, 'Popen', next_launch)
    with pytest.raises(OSError, match='next original launch reached'):
        runner._submit(config, submissions[1])
    assert len(launches) == 1


def test_stopped_worker_completes_existing_terminal_manifest_without_rewriting(tmp_path):
    config, _, run_id, root = _stopped_worker_fixture(tmp_path)
    manifest = dict(started_at='2026-10-28T00:00:01Z', completed_at='2026-10-28T00:00:02Z',
        terminal_state='succeeded', exit_code=0, solver_exit_code=0,
        outputs=[], error='', collection_errors=[], resource_usage={})
    raw = json.dumps(manifest, indent=2).encode()
    (root / 'output_manifest.json').write_bytes(raw)
    assert runner._status(config, run_id)['state'] == 'succeeded'
    assert (root / 'output_manifest.json').read_bytes() == raw
    assert (root / 'done').is_file()


def test_uncertain_solver_launch_keeps_original_run_reserved(tmp_path):
    config, _, run_id, root = _stopped_worker_fixture(tmp_path)
    worker = json.loads((root / 'worker.json').read_bytes())
    worker['solver'] = 'launching'
    (root / 'worker.json').write_text(json.dumps(worker))
    with pytest.raises(RuntimeError, match='uncertain solver launch'):
        runner._status(config, run_id)
    with pytest.raises(RuntimeError, match='uncertain solver launch'):
        runner._cancel(config, run_id)
    assert not (root / 'done').exists()


def test_stopped_solver_does_not_turn_later_collection_loss_into_wall_timeout(tmp_path):
    config, _, run_id, root = _stopped_worker_fixture(tmp_path)
    worker = json.loads((root / 'worker.json').read_bytes())
    worker['solver'] = dict(pid=2**30, boot_id=runner._boot_id(), start_time='1')
    worker['solver_deadline_monotonic'] = time.monotonic() - 1
    (root / 'worker.json').write_text(json.dumps(worker))
    assert runner._status(config, run_id)['state'] == 'failed'
    assert json.loads((root / 'output_manifest.json').read_bytes())['error'] == 'remote_worker_lost'


@pytest.mark.process_e2e
@pytest.mark.parametrize('completion', ['cancel', 'wall_deadline'])
def test_dead_worker_live_solver_is_observed_then_stopped_under_same_run(tmp_path, completion):
    """Real lease death and orphan process-group cancellation; no solver/SSH."""
    config, _, run_id, root = _stopped_worker_fixture(tmp_path)
    (root / 'worker.json').unlink()
    config['runner']['terminate_grace_seconds'] = 0.5
    solver = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                              start_new_session=True)
    worker = None
    reaper = None
    program = r'''
import json, os, runpy, signal, sys
r = runpy.run_path(sys.argv[1])
def held(config, directory, worker):
    worker['solver'] = json.loads(sys.argv[3])
    worker['solver_deadline_monotonic'] = __import__('time').monotonic() + float(sys.argv[4])
    r['_atomic_write'](os.path.join(directory, 'worker.json'), r['_canonical'](worker), 0o440)
    r['_write_new'](os.path.join(directory, 'pid'), (str(os.getpid()) + '\n').encode(), 0o440)
    r['_write_new'](os.path.join(directory, 'ready'), b'', 0o440)
    signal.pause()
r['_run_worker'].__globals__['_run_worker_owned'] = held
r['_run_worker']({}, sys.argv[2])
'''
    try:
        solver_identity = runner._process_identity(solver.pid)
        original = json.loads((root / 'submission.json').read_bytes())
        worker = subprocess.Popen([sys.executable, '-c', program, runner.__file__, str(root),
                                   json.dumps(solver_identity), str(original['limits']['wall_time_seconds'])],
                                  start_new_session=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 3
        while not (root / 'ready').exists() and time.monotonic() < deadline:
            assert worker.poll() is None
            time.sleep(0.01)
        assert (root / 'ready').is_file()
        # A live lease prevents any concurrent terminal repair.
        assert runner._status(config, run_id)['state'] == 'running'
        worker.kill()
        worker.communicate(timeout=3)
        assert runner._status(config, run_id)['state'] == 'running'
        assert solver.poll() is None, 'status unexpectedly killed the orphan solver'
        assert not (root / 'done').exists()
        # This test owns the orphan and can reap it after cancellation; the
        # runner itself never mistakes an extant zombie group for stop proof.
        reaper = threading.Thread(target=solver.wait, kwargs=dict(timeout=3))
        reaper.start()
        if completion == 'cancel':
            result = runner._cancel(config, run_id)
        else:
            # The original accepted deadline, rather than a new execution or
            # user cancellation, authorizes stopping this identified orphan.
            record = json.loads((root / 'worker.json').read_bytes())
            time.sleep(max(0, record['solver_deadline_monotonic'] - time.monotonic()) + 0.02)
            result = runner._status(config, run_id)
        reaper.join(timeout=3)
        assert result['state'] == ('cancelled' if completion == 'cancel' else 'failed')
        assert result['done'] is True
        manifest = json.loads((root / 'output_manifest.json').read_bytes())
        assert manifest['error'] == ('remote_worker_lost' if completion == 'cancel' else 'wall_time_exceeded')
        assert solver.returncode is not None
        assert runner._lookup_submission(config, run_id[4:])['run_id'] == run_id
        assert runner._collect(config, run_id)['run_id'] == run_id
    finally:
        if worker is not None and worker.poll() is None:
            worker.kill()
            worker.communicate(timeout=3)
        if solver.poll() is None:
            os.killpg(solver.pid, signal.SIGKILL)
            solver.wait(timeout=3)
        if reaper is not None:
            reaper.join(timeout=3)
