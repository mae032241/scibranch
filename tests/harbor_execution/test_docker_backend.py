"""Fake bounded subprocess transport; these tests need no Docker daemon."""
import json
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins/harbor_execution"))
from harbor_execution import docker_backend as module


CONTAINER = "a" * 64


def _config(**changes):
    value = dict(container_id=CONTAINER, docker_argv=["docker", "--context", "bound-context"],
                 python_argv=["/usr/local/bin/python", "-B", "-u"], container_root="/tmp/original/calls",
                 max_seconds=30, max_log_bytes=4096, user="0:0", environment={"TASK_FLAG": "original"})
    value.update(changes)
    return value


def _state(**changes):
    value = dict(call_id="call-1", state="running", reason=None, returncode=None, started_at=1.,
                 finished_at=None, deadline_epoch=50., cleanup_confirmed=False,
                 native_identity={"container_id": CONTAINER, "supervisor": {"pid": 123, "start_ticks": 99}})
    value.update(changes)
    return value


class FakeTransport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append((command, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, dict):
            response = (0, json.dumps(response).encode(), b"")
        returncode, stdout, stderr = response
        if kwargs.get("sink"):
            kwargs["sink"](str(len(stdout)).encode() + b"\n")
            kwargs["sink"](stdout)
            stdout = b""
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_launch_uses_only_bound_id_preserves_exec_projection_and_exact_code_export(tmp_path, monkeypatch):
    code = tmp_path / "admitted-code"
    code.mkdir()
    transport = FakeTransport([{"Id": CONTAINER, "State": {"Running": True}}, {"created": True},
                               (0, b"", b""), (0, b"", b""), _state()])
    monkeypatch.setattr(module, "run_bounded", transport)
    backend = module.DockerBackend(_config())
    assert backend.launch("call-1", code, "entry.py", ["--x", "1"], deadline_epoch=50.) == _state()
    commands = [command for command, _ in transport.commands]
    assert commands[0][-1] == CONTAINER and "inspect" in commands[0]
    copies = [command for command in commands if "cp" in command]
    assert copies[0][-2:] == [str(code) + "/.", CONTAINER + ":/tmp/original/calls/call-1/code"]
    assert copies[1][-1] == CONTAINER + ":/tmp/original/calls/call-1/control/runner.py"
    for command in commands:
        assert not any(item in command for item in ("run", "create", "rm", "remove", "compose", "pull"))
        if "exec" in command:
            assert command[command.index("--user") + 1] == "0:0"
            assert "TASK_FLAG=original" in command
            assert command[command.index(CONTAINER) + 1:command.index(CONTAINER) + 4] == ["/usr/local/bin/python", "-B", "-u"]
    request = json.loads(commands[1][-1])
    assert request["deadline_epoch"] == 50.
    assert request["arguments"] == ["--x", "1"]
    assert all(kwargs["max_output_bytes"] == module.MAX_RESPONSE_BYTES for _, kwargs in transport.commands)


def test_duplicate_transfer_claim_never_copies_or_launches_again(tmp_path, monkeypatch):
    transport = FakeTransport([{"Id": CONTAINER, "State": {"Running": True}}, {"created": False},
                               {"Id": CONTAINER, "State": {"Running": True}}, {"runner_present": True}, _state()])
    monkeypatch.setattr(module, "run_bounded", transport)
    backend = module.DockerBackend(_config())
    assert backend.launch("call-1", tmp_path, "entry.py", [], deadline_epoch=50.) == _state()
    assert not any("cp" in command for command, _ in transport.commands)
    assert transport.commands[-1][0][-3:] == ["status", "--call-root", "/tmp/original/calls/call-1"]


def test_status_missing_native_marker_and_stopped_original_remain_unknown(monkeypatch):
    transport = FakeTransport([{"Id": CONTAINER, "State": {"Running": True}}, {"runner_present": False},
                               {"Id": CONTAINER, "State": {"Running": False}}])
    monkeypatch.setattr(module, "run_bounded", transport)
    backend = module.DockerBackend(_config())
    missing = backend.state("call-1")
    assert missing["state"] == "unknown" and missing["cleanup_confirmed"] is False
    stopped = backend.cancel("call-1")
    assert stopped["state"] == "unknown" and stopped["returncode"] is None
    assert stopped["cleanup_confirmed"] is True
    assert not any("cancel" in command for command, _ in transport.commands)


def test_cancel_uses_original_call_and_not_host_client_pid(monkeypatch):
    transport = FakeTransport([{"Id": CONTAINER, "State": {"Running": True}}, {"runner_present": True},
                               _state(), _state(state="cancelling")])
    monkeypatch.setattr(module, "run_bounded", transport)
    assert module.DockerBackend(_config()).cancel("call-1")["state"] == "cancelling"
    assert transport.commands[-1][0][-3:] == ["cancel", "--call-root", "/tmp/original/calls/call-1"]


def test_named_read_streams_once_to_exclusive_file_with_no_partial_on_failure(tmp_path, monkeypatch):
    running = {"Id": CONTAINER, "State": {"Running": True}}
    transport = FakeTransport([running, (0, b"original bytes", b""), running,
                               (3, b"partial", b"missing private path"), running, (4, b"", b"too large")])
    monkeypatch.setattr(module, "run_bounded", transport)
    backend = module.DockerBackend(_config())
    retained = tmp_path / "retained"
    backend.read_file("call-1", "work/artifacts/result.json", retained, max_bytes=20, timeout_seconds=1.)
    assert retained.read_bytes() == b"original bytes"
    assert stat.S_IMODE(retained.stat().st_mode) == 0o400
    assert not retained.with_name(retained.name + ".harbor-part").exists()
    with pytest.raises(FileExistsError):
        backend.read_file("call-1", "work/artifacts/result.json", retained, max_bytes=20, timeout_seconds=1.)
    with pytest.raises(module.FileMissing):
        backend.read_file("call-1", "control/stdout.raw", tmp_path / "missing", max_bytes=20, timeout_seconds=1.)
    assert not (tmp_path / "missing").exists()
    with pytest.raises(module.FileChanged):
        backend.read_file("call-1", "work/artifacts/large", tmp_path / "large", max_bytes=20, timeout_seconds=1.)
    assert not (tmp_path / "large").exists()


def test_read_ignores_legacy_byte_quota_and_removes_interrupted_partial(tmp_path, monkeypatch):
    running = {"Id": CONTAINER, "State": {"Running": True}}
    transport = FakeTransport([running, (0, b"too many bytes", b""), running, TimeoutError("private Docker endpoint")])
    monkeypatch.setattr(module, "run_bounded", transport)
    backend = module.DockerBackend(_config())
    destination = tmp_path / "complete"
    backend.read_file("call-1", "work/output", destination, max_bytes=3, timeout_seconds=1.)
    assert destination.read_bytes() == b"too many bytes"
    assert "--max-bytes" not in transport.commands[1][0]
    with pytest.raises(module.BackendError) as caught:
        backend.read_file("call-1", "work/output", tmp_path / "interrupted", max_bytes=3, timeout_seconds=1.)
    assert caught.value.facts["error_type"] == "TimeoutError"
    assert not (tmp_path / "interrupted").exists()


def test_retry_cleans_only_named_part_and_never_publishes_while_streaming(tmp_path, monkeypatch):
    retained = tmp_path / "retained"
    partial = tmp_path / "retained.harbor-part"
    partial.write_bytes(b"abandoned bytes")
    def transport(command, **kwargs):
        assert not retained.exists()
        if "inspect" in command:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"Id": CONTAINER, "State": {"Running": True}}).encode(), stderr=b"")
        kwargs["sink"](b"14\ncomplete bytes")
        assert not retained.exists()
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    monkeypatch.setattr(module, "run_bounded", transport)
    module.DockerBackend(_config()).read_file("call-1", "work/output", retained, max_bytes=20, timeout_seconds=1.)
    assert retained.read_bytes() == b"complete bytes"
    assert not partial.exists()


def test_file_read_shares_the_explicit_budget_between_inspect_and_stream(tmp_path, monkeypatch):
    clock = [100.]
    calls = []
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    def transport(command, **kwargs):
        calls.append((command, kwargs))
        if "inspect" in command:
            clock[0] += .6
            return SimpleNamespace(returncode=0, stdout=json.dumps({"Id": CONTAINER, "State": {"Running": True}}).encode(), stderr=b"")
        kwargs["sink"](b"8\ncomplete")
        clock[0] += .3
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    monkeypatch.setattr(module, "run_bounded", transport)
    destination = tmp_path / "retained"
    module.DockerBackend(_config()).read_file("call-1", "work/output", destination, max_bytes=20, timeout_seconds=1.)
    assert destination.read_bytes() == b"complete"
    assert calls[0][1]["context"] is calls[1][1]["context"]
    assert calls[0][1]["timeout"] == pytest.approx(1.)
    assert calls[1][1]["timeout"] == pytest.approx(.4)
    assert calls[0][1]["context"].deadline_monotonic == 101.


@pytest.mark.parametrize("slow_phase", ["inspect", "stream"])
def test_file_read_budget_expiry_never_publishes_a_final(tmp_path, monkeypatch, slow_phase):
    clock = [100.]
    calls = []
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    def transport(command, **kwargs):
        calls.append(command)
        if "inspect" in command:
            clock[0] += 1.1 if slow_phase == "inspect" else .4
            return SimpleNamespace(returncode=0, stdout=json.dumps({"Id": CONTAINER, "State": {"Running": True}}).encode(), stderr=b"")
        kwargs["sink"](b"7\npartial")
        clock[0] += .7
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    monkeypatch.setattr(module, "run_bounded", transport)
    destination = tmp_path / "retained"
    with pytest.raises(module.BackendError) as caught:
        module.DockerBackend(_config()).read_file("call-1", "work/output", destination, max_bytes=20, timeout_seconds=1.)
    assert caught.value.facts["error_type"] == "TimeoutError"
    assert len(calls) == (1 if slow_phase == "inspect" else 2)
    assert not destination.exists()
    assert not destination.with_name(destination.name + ".harbor-part").exists()


def test_invalid_named_file_is_typed_and_transport_failure_remains_uncertain(tmp_path, monkeypatch):
    running = {"Id": CONTAINER, "State": {"Running": True}}
    transport = FakeTransport([running, (5, b"", b""), running, (2, b"", b"private infrastructure failure")])
    monkeypatch.setattr(module, "run_bounded", transport)
    backend = module.DockerBackend(_config())
    with pytest.raises(module.FileInvalid):
        backend.read_file("call-1", "work/artifacts/invalid", tmp_path / "invalid", max_bytes=20, timeout_seconds=1.)
    with pytest.raises(module.BackendError) as caught:
        backend.read_file("call-1", "work/artifacts/failure", tmp_path / "failed", max_bytes=20, timeout_seconds=1.)
    assert not isinstance(caught.value, (module.FileInvalid, module.FileMissing, module.FileTooLarge))
    assert not (tmp_path / "invalid").exists() and not (tmp_path / "failed").exists()


def test_binding_mismatch_private_transport_error_and_path_escape_fail_closed(tmp_path, monkeypatch):
    transport = FakeTransport([{"Id": "b" * 64, "State": {"Running": True}}])
    monkeypatch.setattr(module, "run_bounded", transport)
    backend = module.DockerBackend(_config())
    with pytest.raises(module.BackendError):
        backend.state("call-1")
    with pytest.raises(ValueError):
        backend.read_file("../different", "work/file", tmp_path / "x", max_bytes=1, timeout_seconds=1.)
    with pytest.raises(ValueError):
        backend.read_file("call-1", "../different", tmp_path / "x", max_bytes=1, timeout_seconds=1.)
    with pytest.raises(ValueError):
        module.DockerBackend(_config(container_id="short"))
    with pytest.raises(ValueError):
        module.DockerBackend(_config(container_root="/app/scratch"))


def test_nonroot_config_or_original_container_user_rejected_before_upload(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="only the original root effective user"):
        module.DockerBackend(_config(user="1000:1000"))
    transport = FakeTransport([{"Id": CONTAINER, "State": {"Running": True}, "Config": {"User": "scientist"}}])
    monkeypatch.setattr(module, "run_bounded", transport)
    with pytest.raises(module.UnsupportedUser, match="only the original root effective user"):
        module.DockerBackend(_config(user=None)).launch("call-1", tmp_path, "entry.py", [], deadline_epoch=50.)
    assert len(transport.commands) == 1 and "inspect" in transport.commands[0][0]


def test_chunked_read_keeps_complete_original_above_legacy_limit(tmp_path, monkeypatch):
    blocks = [b"first", b"second", b"last"]
    def transport(command, **kwargs):
        if "inspect" in command:
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                {"Id": CONTAINER, "State": {"Running": True}}).encode(), stderr=b"")
        assert kwargs["sink"] is not None
        kwargs["sink"](b"1")
        kwargs["sink"](b"5\n")
        for block in blocks:
            kwargs["sink"](block)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    monkeypatch.setattr(module, "run_bounded", transport)
    destination = tmp_path / "original"
    module.DockerBackend(_config(max_log_bytes=1)).read_file(
        "call-1", "control/stdout.raw", destination, max_bytes=1, timeout_seconds=1.)
    assert destination.read_bytes() == b"".join(blocks)


@pytest.mark.parametrize("declared,payload", [(9, b"short"), (2, b"long")])
def test_exact_source_size_mismatch_never_publishes_a_final(tmp_path, monkeypatch, declared, payload):
    def transport(command, **kwargs):
        if "inspect" in command:
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                {"Id": CONTAINER, "State": {"Running": True}}).encode(), stderr=b"")
        kwargs["sink"](str(declared).encode() + b"\n")
        kwargs["sink"](payload)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    monkeypatch.setattr(module, "run_bounded", transport)
    destination = tmp_path / "original"
    with pytest.raises(module.FileChanged):
        module.DockerBackend(_config()).read_file(
            "call-1", "work/artifacts/result.json", destination, timeout_seconds=1.)
    assert not destination.exists()
    assert not destination.with_name(destination.name + ".harbor-part").exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize("runner_mode", ["python", "shell"])
@pytest.mark.parametrize("payload", [b"", b"complete old-call original\n"])
def test_readonly_transfer_collects_old_frozen_call_without_upgrading_or_relaunching(
        tmp_path, monkeypatch, runner_mode, payload):
    root = tmp_path / "calls/call-1"
    control = root / "control"
    control.mkdir(parents=True)
    legacy = control / ("runner.py" if runner_mode == "python" else "runner.sh")
    # A historical supervisor can require --max-bytes and emit unframed bytes.
    # Collection must never invoke or replace it.
    frozen = (b"raise AssertionError('old runner must not be invoked; --max-bytes raw protocol')\n"
              if runner_mode == "python" else b"exit 99 # historical raw read-file protocol\n")
    legacy.write_bytes(frozen)
    (control / "stdout.raw").write_bytes(payload)
    commands = []
    def transport(command, **kwargs):
        commands.append(command)
        if "inspect" in command:
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                {"Id": CONTAINER, "State": {"Running": True}}).encode(), stderr=b"")
        assert "cp" not in command and "launch" not in command
        assert str(legacy) not in command
        argv = command[command.index(CONTAINER) + 1:]
        result = subprocess.run(argv, capture_output=True, timeout=2)
        for offset in range(0, len(result.stdout), 3):
            kwargs["sink"](result.stdout[offset:offset + 3])
        return SimpleNamespace(returncode=result.returncode, stdout=b"", stderr=result.stderr)
    monkeypatch.setattr(module, "run_bounded", transport)
    destination = tmp_path / "retained"
    module.DockerBackend(_config(container_root=str(tmp_path / "calls"),
        python_argv=[sys.executable, "-B", "-u"], runner_mode=runner_mode)).read_file(
        "call-1", "control/stdout.raw", destination, timeout_seconds=3.)
    assert destination.read_bytes() == payload
    assert legacy.read_bytes() == frozen
    assert len(commands) == 2
