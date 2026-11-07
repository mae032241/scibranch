"""Real finite runner processes; no Docker daemon or external packages."""
import json
import io
import errno
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins/harbor_execution"))
from harbor_execution import runner


def _call(tmp_path, script, *, seconds=5, log_bytes=4096):
    root = tmp_path / "original-call"
    request = dict(call_id="original-call", container_id="a" * 64, entry="science.py", arguments=[],
                   python_argv=[sys.executable, "-B", "-u"], deadline_epoch=time.time() + seconds,
                   max_log_bytes=log_bytes)
    assert runner.prepare(root, request)
    (root / "code/science.py").write_text(script)
    shutil.copyfile(runner.__file__, root / "control/runner.py")
    return root


def _command(root, operation, *args):
    result = subprocess.run([sys.executable, "-B", str(root / "control/runner.py"), operation,
                             "--call-root", str(root), *args], capture_output=True, timeout=8)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _terminal(root, timeout=8):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        state = _command(root, "status")
        if state["state"] in ("succeeded", "failed", "cancelled"):
            assert state["cleanup_confirmed"] is True
            return state
        time.sleep(.03)
    raise AssertionError("finite runner did not publish a cleaned terminal receipt")


def _stop(root):
    if (root / "control/identity.json").exists():
        _command(root, "cancel")
        _terminal(root)


@pytest.mark.process_e2e
def test_native_launch_records_real_exit_cwd_raw_logs_and_output(tmp_path):
    root = _call(tmp_path, "import json,os,pathlib,sys\n"
                 "assert pathlib.Path.cwd().name == 'work'\n"
                 "pathlib.Path('artifacts').mkdir()\n"
                 "pathlib.Path('artifacts/result.json').write_text(json.dumps({'ok': True}))\n"
                 "print('native stdout');print('native stderr',file=sys.stderr)\n")
    try:
        launched = _command(root, "launch")
        terminal = _terminal(root)
        assert launched["call_id"] == terminal["call_id"] == "original-call"
        assert terminal["state"] == "succeeded" and terminal["returncode"] == 0
        assert terminal["native_identity"]["science"]["pid"] != terminal["native_identity"]["supervisor"]["pid"]
        assert terminal["native_identity"]["science"]["start_ticks"] > 0
        assert (root / "control/stdout.raw").read_bytes() == b"native stdout\n"
        assert (root / "control/stderr.raw").read_bytes() == b"native stderr\n"
        assert json.loads((root / "work/artifacts/result.json").read_text()) == {"ok": True}
    finally:
        _stop(root)


@pytest.mark.process_e2e
def test_duplicate_launch_uses_original_claim_and_does_not_execute_twice(tmp_path):
    root = _call(tmp_path, "import pathlib,time\n"
                 "p=pathlib.Path('count');p.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
                 "time.sleep(.3)\n")
    try:
        first = _command(root, "launch")
        original_deadline = first["deadline_epoch"]
        again = _command(root, "launch")
        terminal = _terminal(root)
        assert first["native_identity"]["supervisor"] == again["native_identity"]["supervisor"]
        assert terminal["deadline_epoch"] == original_deadline
        assert (root / "work/count").read_text() == "x"
        assert not runner.prepare(root, runner.read_json(root / "control/request.json"))
        assert _command(root, "launch")["native_identity"] == terminal["native_identity"]
    finally:
        _stop(root)


@pytest.mark.process_e2e
def test_unknown_claim_cannot_be_relaunched_and_status_does_not_take_launch_lock(tmp_path):
    import fcntl
    root = _call(tmp_path, "raise AssertionError('must not run')\n")
    runner.atomic_json(root / "control/launch.json", {"call_id": "original-call", "claimed_at": time.time()})
    with (root / "control/launch.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert _command(root, "status")["state"] == "unknown"
    assert _command(root, "launch")["state"] == "unknown"
    assert not (root / "control/identity.json").exists()


@pytest.mark.process_e2e
def test_cancel_stops_original_science_and_setsid_descendant(tmp_path):
    root = _call(tmp_path, "import pathlib,subprocess,sys,time\n"
                 "p=subprocess.Popen([sys.executable,'-c',\"import os,pathlib,signal,time;os.setsid();"
                 "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                 "pathlib.Path('descendant.ready').write_text('ready');time.sleep(30)\"])\n"
                 "pathlib.Path('descendant.pid').write_text(str(p.pid))\n"
                 "time.sleep(30)\n")
    try:
        _command(root, "launch")
        until = time.monotonic() + 3
        while not (root / "work/descendant.ready").exists() and time.monotonic() < until:
            time.sleep(.02)
        assert (root / "work/descendant.ready").exists()
        pid = int((root / "work/descendant.pid").read_text())
        descendant = runner.process_identity(pid)
        assert _command(root, "cancel")["state"] in ("cancelling", "cancelled")
        terminal = _terminal(root)
        assert terminal["state"] == "cancelled"
        assert terminal["returncode"] is not None and terminal["returncode"] != 0
        assert not runner.process_matches(descendant)
    finally:
        _stop(root)


@pytest.mark.process_e2e
def test_deadline_survives_client_exit_preserves_logs_and_bounds_descendants(tmp_path):
    root = _call(tmp_path, "import pathlib,subprocess,sys,time\n"
                 "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
                 "pathlib.Path('descendant.pid').write_text(str(p.pid))\n"
                 "print('x'*20000,flush=True)\n"
                 "time.sleep(30)\n", seconds=.8, log_bytes=128)
    try:
        launched = _command(root, "launch")
        # The launch client has already exited. No host polling supplies the
        # scientific deadline to the independently finite supervisor.
        time.sleep(1)
        terminal = _terminal(root)
        assert terminal["state"] == "failed" and terminal["reason"] == "timeout"
        assert terminal["deadline_epoch"] == launched["deadline_epoch"]
        assert terminal["log_truncated"]["stdout"] is False
        assert (root / "control/stdout.raw").read_bytes() == b"x" * 20000 + b"\n"
        pid = int((root / "work/descendant.pid").read_text())
        try:
            assert runner.process_identity(pid)["state"] == "Z"
        except FileNotFoundError:
            pass
    finally:
        _stop(root)


@pytest.mark.process_e2e
def test_normal_exit_cleans_background_writer_before_terminal(tmp_path):
    root = _call(tmp_path, "import pathlib,subprocess,sys\n"
                 "p=subprocess.Popen([sys.executable,'-c','import os,time;os.setsid();time.sleep(30)'])\n"
                 "pathlib.Path('descendant.pid').write_text(str(p.pid))\n")
    try:
        _command(root, "launch")
        terminal = _terminal(root)
        assert terminal["state"] == "succeeded" and terminal["returncode"] == 0
        pid = int((root / "work/descendant.pid").read_text())
        try:
            assert runner.process_identity(pid)["state"] == "Z"
        except FileNotFoundError:
            pass
    finally:
        _stop(root)


def test_named_file_read_rejects_symlink_escape_and_ignores_legacy_byte_quota(tmp_path, monkeypatch):
    root = _call(tmp_path, "pass\n")
    (root / "work/file").write_bytes(b"12345")
    (root / "work/link").symlink_to(tmp_path)
    with pytest.raises((OSError, ValueError)):
        runner.open_regular(root, "work/link/outside")
    output = io.BytesIO()
    monkeypatch.setattr(runner.sys, "stdout", type("Output", (), {"buffer": output})())
    assert runner.stream_file(root, "work/file", 4) == 0
    assert output.getvalue() == b"5\n12345"
    with pytest.raises(ValueError):
        runner.open_regular(root, "../outside")


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_invalid_authored_named_output_has_distinct_fixed_exit_code(tmp_path, kind):
    root = _call(tmp_path, "pass\n")
    path = root / "work/invalid"
    if kind == "symlink":
        (root / "work/target").write_bytes(b"bytes")
        path.symlink_to("target")
    elif kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    assert runner.main(["read-file", "--call-root", str(root), "--relative", "work/invalid", "--max-bytes", "20"]) == 5
    assert runner.main(["read-file", "--call-root", str(root), "--relative", "../outside", "--max-bytes", "20"]) == 5


def test_native_read_infrastructure_error_remains_generic(tmp_path, monkeypatch):
    root = _call(tmp_path, "pass\n")
    def failure(*args):
        raise OSError(errno.EIO, "private infrastructure failure")
    monkeypatch.setattr(runner, "stream_file", failure)
    assert runner.main(["read-file", "--call-root", str(root), "--relative", "work/file", "--max-bytes", "20"]) == 2


@pytest.mark.parametrize("mutation", ["grow", "truncate"])
def test_stream_file_rejects_late_source_size_change(tmp_path, monkeypatch, mutation):
    root = _call(tmp_path, "pass\n")
    path = root / "work/file"
    path.write_bytes(b"original")
    class Output:
        def write(self, block):
            if block == b"8\n":
                return
            if mutation == "grow":
                with path.open("ab") as stream:
                    stream.write(b"late")
            else:
                path.write_bytes(b"cut")
    monkeypatch.setattr(runner.sys, "stdout", type("Stdout", (), {"buffer": Output()})())
    assert runner.stream_file(root, "work/file", 1) == 4
