"""Explicit cohort transport shapes; no task solution or verifier evidence."""
from __future__ import annotations

import importlib.util
import asyncio
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import time
import types

import pytest


PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "harbor_execution"
sys.path.insert(0, str(PLUGIN))
from harbor_execution.config import validate
from harbor_execution import docker_backend
from harbor_execution.runner import SHELL_RUNNER


@pytest.fixture
def entry(monkeypatch):
    for name in ("harbor", "harbor.agents", "harbor.agents.base", "harbor.environments",
                 "harbor.environments.docker", "harbor.environments.docker.docker"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["harbor.agents.base"].BaseAgent = object
    sys.modules["harbor.environments.docker.docker"].DockerEnvironment = object
    spec = importlib.util.spec_from_file_location("_statistics_host_entry", PLUGIN / "harbor_execution/harbor_agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tar(path, files):
    with tarfile.open(path, "w") as archive:
        for name, content in files:
            item = tarfile.TarInfo(name)
            if content is None:
                item.type, item.linkname = tarfile.SYMTYPE, "/root/task_contract.json"
                archive.addfile(item)
            else:
                item.size = len(content)
                archive.addfile(item, io.BytesIO(content))
    return path


def test_r_bundle_delivers_both_exact_original_targets(entry, tmp_path):
    archive = _tar(tmp_path / "delivery.tar", [("helper_functions.R", b"helper\n"),
        ("starter_code.R", b"starter\n")])
    files = entry.bundle_delivery_files(archive, "highdim-mediation-debiasing", tmp_path / "selected")
    assert [(target, source.read_bytes()) for source, target in files] == [
        ("/root/project/helper_functions.R", b"helper\n"), ("/root/project/starter_code.R", b"starter\n")]


def test_directory_bundle_preserves_nested_resources_without_contract_rewrite(entry, tmp_path):
    archive = _tar(tmp_path / "delivery.tar", [("small_area_submission/solve.py", b"solve\n"),
        ("small_area_submission/resources/weights.csv", b"resource\n")])
    files = entry.bundle_delivery_files(archive, "small-area-equivalence", tmp_path / "selected")
    assert {target for _, target in files} == {"/app/small_area_submission/solve.py",
        "/app/small_area_submission/resources/weights.csv"}
    assert all(not target.startswith("/root/") for _, target in files)


@pytest.mark.parametrize("files", [
    [("starter_code.R", b"incomplete")],
    [("starter_code.R", b"first"), ("starter_code.R", b"second"), ("helper_functions.R", b"helper")],
    [("../starter_code.R", b"escape"), ("helper_functions.R", b"helper")],
    [("starter_code.R", None), ("helper_functions.R", b"helper")],
    [("starter_code.R", b"starter"), ("helper_functions.R", b"helper"), ("evaluation_contract.md", b"overwrite")],
])
def test_r_bundle_rejects_missing_duplicate_outside_or_link_entries_before_materializing(entry, tmp_path, files):
    archive = _tar(tmp_path / "delivery.tar", files)
    destination = tmp_path / "selected"
    with pytest.raises(ValueError):
        entry.bundle_delivery_files(archive, "highdim-mediation-debiasing", destination)
    assert not destination.exists()


@pytest.mark.parametrize("extra", ["/root/task_contract.json", "small_area_submission/../../task_contract.json",
    "task_contract.json"])
def test_directory_bundle_cannot_publish_original_contract_paths(entry, tmp_path, extra):
    archive = _tar(tmp_path / "delivery.tar", [("small_area_submission/solve.py", b"solve"), (extra, b"changed")])
    with pytest.raises(ValueError):
        entry.bundle_delivery_files(archive, "small-area-equivalence", tmp_path / "selected")


def test_shapes_keep_original_r_and_python_commands_and_public_contracts(entry):
    assert entry.task_shape("highdim-mediation-debiasing")["argv"] == ["/usr/bin/Rscript", "--vanilla"]
    assert entry.task_shape("highdim-mediation-debiasing")["runtime"] == "shell"
    assert entry.task_shape("symbolic-regression")["artifact"] == "/app/regressor.py"
    small = entry.task_shape("small-area-equivalence")
    assert small["runtime"] == "python"
    assert ("/root/task_contract.json", "task_contract.json") in small["public_files"]
    assert "utils::tar" in entry.task_navigation("highdim-mediation-debiasing")
    with pytest.raises(ValueError):
        entry.task_shape("guessed-task")


def test_shell_transport_retains_original_claim_and_r_argv_without_container_python(monkeypatch, tmp_path):
    container = "a" * 64
    config = validate(dict(container_id=container, container_root="/tmp/original/calls",
        runner_mode="shell", python_argv=["/usr/bin/Rscript", "--vanilla"]))
    native = dict(call_id="original-call", state="running", returncode=None, cleanup_confirmed=False,
        native_identity={"container_id": container})
    responses = iter([{"Id": container, "State": {"Running": True}}, {"created": True},
        None, None, native, {"Id": container, "State": {"Running": True}}, {"created": False},
        {"Id": container, "State": {"Running": True}}, {"runner_present": True}, native])
    commands = []
    def transport(argv, **kwargs):
        commands.append(argv)
        response = next(responses)
        return types.SimpleNamespace(returncode=0, stdout=b"" if response is None else json.dumps(response).encode(), stderr=b"")
    monkeypatch.setattr(docker_backend, "run_bounded", transport)
    backend = docker_backend.DockerBackend(config)
    assert backend.launch("original-call", tmp_path, "entry.R", ["quoted argument"], deadline_epoch=time.time() + 30) == native
    copies = [argv for argv in commands if "cp" in argv]
    assert len(copies) == 2 and copies[-1][-1].endswith("/control/runner.sh")
    settings = commands[1][-1]
    assert "/usr/bin/Rscript --vanilla" in settings and "entry.R" in settings
    assert "'quoted argument'" in settings
    assert backend.launch("original-call", tmp_path, "entry.R", [], deadline_epoch=time.time() + 30) == native
    assert len([argv for argv in commands if "cp" in argv]) == 2
    assert commands[-1][-2:] == ["status", "/tmp/original/calls/original-call"]
    assert all("/usr/local/bin/python" not in argv for argv in commands)


@pytest.mark.process_e2e
@pytest.mark.parametrize("exit_code,stdout,stderr", [
    (0, "1234567890", "stderr"), (7, "1234567890", "stderr"),
    (0, "1234", "stde")])
def test_native_shell_payload_preserves_real_exit_and_complete_logs(tmp_path, exit_code, stdout, stderr):
    root = tmp_path / "original-call"
    for name in ("code", "work", "control"):
        (root / name).mkdir(parents=True)
    script = root / "control/runner.sh"
    script.write_text(SHELL_RUNNER)
    science = root / "code/science.sh"
    science.write_text("printf %s " + shlex.quote(stdout) + "; printf %s " + shlex.quote(stderr)
        + " >&2; exit " + str(exit_code) + "\n")
    deadline = time.time() + 5
    (root / "control/request.sh").write_text("\n".join([
        "call_id=original-call", "container_id=" + "a" * 64, "deadline_epoch=" + str(deadline),
        "deadline_seconds=" + str(int(deadline)), "max_log_bytes=4",
        "science_argv=(" + shlex.join(["/bin/bash", str(science)]) + ")\n"]))
    subprocess.run(["/bin/bash", str(script), "launch", str(root)], capture_output=True, check=True, timeout=4)
    until = time.monotonic() + 6
    terminal = root / "control/terminal.json"
    while not terminal.exists() and time.monotonic() < until:
        time.sleep(.03)
    assert terminal.exists()
    receipt = json.loads(terminal.read_text())
    assert receipt["state"] == ("succeeded" if exit_code == 0 else "failed")
    assert receipt["returncode"] == exit_code and receipt["cleanup_confirmed"] is True
    assert receipt["log_truncated"] is False
    assert (root / "control/stdout.raw").read_bytes() == stdout.encode()
    assert (root / "control/stderr.raw").read_bytes() == stderr.encode()


@pytest.mark.process_e2e
@pytest.mark.parametrize("stop", ["timeout", "cancel"])
def test_native_shell_payload_stops_original_group_with_live_child_and_never_relaunches(tmp_path, stop):
    root = tmp_path / "original-call"
    for name in ("code", "work", "control"):
        (root / name).mkdir(parents=True)
    script = root / "control/runner.sh"
    script.write_text(SHELL_RUNNER)
    science = root / "code/science.sh"
    science.write_text("printf x >> launches\nsleep 30 &\nchild=$!\n"
        "trap 'wait \"$child\" 2>/dev/null; exit 143' TERM\n"
        "printf '%s %s\\n' \"$$\" \"$child\" > process.pids\nwait \"$child\"\n")
    deadline = time.time() + (4 if stop == "timeout" else 15)
    (root / "control/request.sh").write_text("\n".join([
        "call_id=original-call", "container_id=" + "a" * 64, "deadline_epoch=" + str(deadline),
        "deadline_seconds=" + str(int(deadline)), "max_log_bytes=4096",
        "science_argv=(" + shlex.join(["/bin/bash", str(science)]) + ")\n"]))
    def command(operation):
        result = subprocess.run(["/bin/bash", str(script), operation, str(root)],
            capture_output=True, check=True, timeout=4)
        return json.loads(result.stdout)
    terminal = root / "control/terminal.json"
    try:
        command("launch")
        ready = root / "work/process.pids"
        until = time.monotonic() + 2
        while not ready.exists() and time.monotonic() < until:
            time.sleep(.03)
        assert ready.exists(), "original science and same-group child did not start"
        parent_pid, child_pid = map(int, ready.read_text().split())
        os.kill(parent_pid, 0)
        os.kill(child_pid, 0)
        assert os.getpgid(parent_pid) == os.getpgid(child_pid)
        if stop == "cancel":
            assert command("cancel")["state"] in {"cancelling", "cancelled"}
        until = time.monotonic() + 8
        while not terminal.exists() and time.monotonic() < until:
            time.sleep(.03)
        assert terminal.exists(), "finite original group did not publish cleaned terminal state"
        receipt = json.loads(terminal.read_text())
        assert receipt["state"] == ("failed" if stop == "timeout" else "cancelled")
        assert receipt["reason"] == ("timeout" if stop == "timeout" else "cancelled")
        assert receipt["cleanup_confirmed"] is True
        for pid in (parent_pid, child_pid):
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        assert command("launch") == receipt
        assert (root / "work/launches").read_text() == "x"
    finally:
        command("cancel")


def test_delivery_tar_ignores_retired_file_and_total_byte_limits(entry, tmp_path, monkeypatch):
    monkeypatch.setattr(entry, "MAX_BUNDLE_FILE_BYTES", 1, raising=False)
    monkeypatch.setattr(entry, "MAX_BUNDLE_BYTES", 1, raising=False)
    archive = _tar(tmp_path / "delivery.tar", [("starter_code.R", b"complete starter"),
        ("helper_functions.R", b"complete helper")])
    files = entry.bundle_delivery_files(archive, "highdim-mediation-debiasing", tmp_path / "selected")
    assert [source.read_bytes() for source, _ in files] == [b"complete helper", b"complete starter"]


@pytest.mark.parametrize("payload", [b"short", b"too many bytes for declaration"])
def test_delivery_tar_preserves_exact_declared_size_guard(entry, tmp_path, monkeypatch, payload):
    archive_path = _tar(tmp_path / "delivery.tar", [("starter_code.R", b"123456"),
        ("helper_functions.R", b"helper")])
    original = tarfile.TarFile.extractfile
    def changed(archive, item):
        return io.BytesIO(payload) if item.name == "starter_code.R" else original(archive, item)
    monkeypatch.setattr(tarfile.TarFile, "extractfile", changed)
    with pytest.raises(ValueError, match="declared bound|incomplete"):
        entry.bundle_delivery_files(archive_path, "highdim-mediation-debiasing", tmp_path / "selected")


class _ExtractionClock:
    def __init__(self):
        self.now = 0.

    def remaining_seconds(self):
        if self.now >= 1.:
            raise TimeoutError("synthetic extraction deadline")
        return 1. - self.now


def test_delivery_tar_deadline_covers_metadata_and_unselected_member_skip(entry, tmp_path, monkeypatch):
    archive = _tar(tmp_path / "delivery.tar", [("starter_code.R", b"starter"),
        ("helper_functions.R", b"helper")])
    context = _ExtractionClock()
    class Source(io.BytesIO):
        def seek(self, offset, whence=0):
            value = super().seek(offset, whence)
            if offset > 512:
                context.now = 2.
            return value
    source = Source(archive.read_bytes())
    original_open = Path.open
    def opened(path, *args, **kwargs):
        return source if path == archive else original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", opened)
    destination = tmp_path / "selected"
    with pytest.raises(TimeoutError, match="synthetic extraction deadline"):
        entry.bundle_delivery_files(archive, "highdim-mediation-debiasing", destination, context=context)
    assert not destination.exists()


def test_delivery_tar_deadline_covers_each_extracted_chunk(entry, tmp_path, monkeypatch):
    archive = _tar(tmp_path / "delivery.tar", [("starter_code.R", b"starter"),
        ("helper_functions.R", b"helper")])
    context = _ExtractionClock()
    class Source(io.BytesIO):
        def read(self, size=-1):
            value = super().read(size)
            context.now = 2.
            return value
    monkeypatch.setattr(tarfile.TarFile, "extractfile", lambda archive, member: Source(b"helper"))
    destination = tmp_path / "selected"
    with pytest.raises(TimeoutError, match="synthetic extraction deadline"):
        entry.bundle_delivery_files(archive, "highdim-mediation-debiasing", destination, context=context)
    assert (destination / "helper_functions.R").read_bytes() == b""


def test_async_tar_cancellation_waits_for_extractor_checkpoint(entry, tmp_path, monkeypatch):
    import threading
    started, stopped = threading.Event(), threading.Event()
    def extraction(*args, context):
        started.set()
        try:
            while True:
                context.remaining_seconds()
                time.sleep(.001)
        finally:
            stopped.set()
    monkeypatch.setattr(entry, "bundle_delivery_files", extraction)
    async def scenario():
        task = asyncio.create_task(entry._bundle_delivery_files(
            tmp_path / "unused.tar", "highdim-mediation-debiasing", tmp_path / "selected", timeout=1.))
        while not started.is_set():
            await asyncio.sleep(.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()
    asyncio.run(scenario())
