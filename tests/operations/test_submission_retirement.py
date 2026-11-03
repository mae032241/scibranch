"""An absence observation is insufficient; retirement must fence late submits."""
import hashlib
import threading

import pytest

from tests.operations.test_experiment_approval_handoff import experiment, start
from tests.operations.test_approval_wait_recovery import execution


def test_worker_can_retire_unknown_submission_and_release_reservation(tmp_path, monkeypatch):
    f = experiment(tmp_path, monkeypatch, seconds=60)
    def rejected(*args, **kwargs):
        raise RuntimeError("submission rejected before creation")
    monkeypatch.setattr(f.adapter, "submit", rejected)
    retired = []
    monkeypatch.setattr(f.adapter, "retire_submission", lambda descriptor: retired.append(descriptor.sha256), raising=False)
    first = start(f)
    assert first["submission"]["state"] == "unknown"
    eid = execution(f).object_id
    assert f.runtime.executions.budget_needs_settlement(eid)
    result = f.worker.call_tool("worker_experiment_execute", {"action": "cancel"})
    assert result["state"] == "abandoned"
    assert result["submission"]["state"] == "not_submitted"
    assert len(retired) == 1
    accounting = f.runtime.executions.budget_accounting(eid)
    assert accounting["actual"] == {key: 0 for key in accounting["requested"]}
    assert not accounting["unsettled_reservation"]
    monkeypatch.setattr(f.adapter, "retire_submission", lambda *_: pytest.fail("already retired"))
    assert f.worker.call_tool("worker_experiment_execute", {"action": "cancel"})["state"] == "abandoned"


def test_retirement_failure_preserves_identity_and_budget(tmp_path, monkeypatch):
    f = experiment(tmp_path, monkeypatch, seconds=60)
    def unavailable(*args, **kwargs):
        raise TimeoutError("remote unavailable")
    monkeypatch.setattr(f.adapter, "submit", unavailable)
    monkeypatch.setattr(f.adapter, "retire_submission", unavailable, raising=False)
    assert start(f)["submission"]["state"] == "unknown"
    eid = execution(f).object_id
    before = f.runtime.executions.budget_accounting(eid)
    with pytest.raises(Exception):
        f.worker.call_tool("worker_experiment_execute", {"action": "cancel"})
    assert f.runtime.executions.status(eid).state == "authorized"
    assert f.runtime.executions.budget_accounting(eid) == before


def test_retirement_that_finds_job_cancels_without_refunding_unknown_usage(tmp_path, monkeypatch):
    f = experiment(tmp_path, monkeypatch, seconds=60)
    def unavailable(*args, **kwargs):
        raise TimeoutError("response lost")
    monkeypatch.setattr(f.adapter, "submit", unavailable)
    monkeypatch.setattr(f.adapter, "retire_submission", lambda _: ("accepted", "running"), raising=False)
    cancelled = []
    monkeypatch.setattr(f.adapter, "cancel", lambda external: cancelled.append(external) or "cancelled", raising=False)
    assert start(f)["submission"]["state"] == "unknown"
    eid = execution(f).object_id
    f.coordinator.bridge.cancel(execution_id=eid)
    assert cancelled == ["accepted"]
    assert f.runtime.executions.status(eid).external_run_id == "accepted"
    assert f.runtime.executions.status(eid).state == "cancelled"
    assert f.runtime.executions.budget_accounting(eid)["actual"] == {}


@pytest.mark.parametrize('method', ['tcad_submit', 'tcad_submit_retirable'])
def test_remote_retirement_fences_submit_still_validating(tmp_path, monkeypatch, method):
    from tcad_artifact import remote_runner_py36 as runner
    job = {"tool_profile": "fixture", "solver_kind": "sprocess", "capability_sha256": "a"*64,
        "input_archive": {"local_path": str(tmp_path / "archive"), "sha256": "b"*64, "size_bytes": 0}}
    raw = runner._canonical(job)
    digest = hashlib.sha256(raw).hexdigest()
    config = {"state_root": str(tmp_path / "state"), "result_root": str(tmp_path / "runs"),
        "exchange_root": str(tmp_path), "runner": {"transfer_chunk_bytes": 1024}}
    monkeypatch.setattr(runner, "_read_bound_descriptor", lambda *_: raw)
    monkeypatch.setattr(runner, "_validate_job", lambda *_: None)
    monkeypatch.setattr(runner, "_validate_authorization", lambda *_: None)
    monkeypatch.setattr(runner, "_tool", lambda *_: {"solver_kind": "sprocess"})
    monkeypatch.setattr(runner, "_capability_sha256", lambda *_: "a"*64)
    entered, release = threading.Event(), threading.Event()
    def validation(*_):
        entered.set()
        assert release.wait(5)
        return "b"*64, 0
    monkeypatch.setattr(runner, "_file_identity", validation)
    failures = []
    def submit():
        response = runner._rpc(config, {"request": {"jsonrpc": "2.0", "id": 1,
            "method": "tools/call", "params": {"name": method, "arguments": {"submission": {}}}}})
        if "error" in response:
            failures.append(response["error"])
    thread = threading.Thread(target=submit)
    thread.start()
    try:
        assert entered.wait(5)
        assert runner._lookup_submission(config, digest) == {"found": False}
        retired = runner._retire_submission(config, digest)
        assert retired == {"found": False, "retired": True, "submission_sha256": digest}
        assert runner._retire_submission(config, digest) == retired
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert len(failures) == 1 and "permanently retired" in str(failures[0])
    assert not (tmp_path / "runs" / ("run_" + digest[:32])).exists()


def test_retiring_existing_remote_job_returns_it_without_fencing(tmp_path, monkeypatch):
    from tcad_artifact import remote_runner_py36 as runner
    digest = "a"*64
    config = {"state_root": str(tmp_path / "state"), "result_root": str(tmp_path / "runs")}
    run_id = "run_" + digest[:32]
    (tmp_path / "runs" / run_id).mkdir(parents=True)
    monkeypatch.setattr(runner, "_status", lambda *_: {"run_id": run_id, "state": "running"})
    assert runner._retire_submission(config, digest) == {"found": True, "run_id": run_id, "state": "running"}
    assert not (tmp_path / "state" / "retired_submissions" / digest).exists()


def test_local_retirement_survives_service_reopen(tmp_path):
    import json
    from tcad_artifact.execution_control import TCADExecutionFacade, TCADExecutionPolicy
    from tests.operations.tcad_policy_fixtures import policy_fields
    fields = {**policy_fields(), "allowed_input_roots": [str(tmp_path)], "tools": [{
        "profile_id": "fixture", "solver_kind": "sprocess", "executable": "/bin/true",
        "public_release_label": "Test fixture", "release_evidence": "Test fixture"}]}
    policy = TCADExecutionPolicy.model_validate_json(json.dumps(fields), strict=True)
    first = TCADExecutionFacade(policy=policy, state_root=tmp_path)
    expected = {"found": False, "retired": True, "submission_sha256": "a"*64}
    assert first.tcad_retire_submission(submission_sha256="a"*64) == expected
    second = TCADExecutionFacade(policy=policy, state_root=tmp_path)
    assert second.tcad_retire_submission(submission_sha256="a"*64) == expected


def test_legacy_unknown_cannot_gain_retirement_from_adapter_upgrade(tmp_path, monkeypatch):
    import json
    from scidiscovery.artifact_agent.service.engineering_diagnostics import atomic_json
    f = experiment(tmp_path, monkeypatch, seconds=60)
    def unavailable(*args, **kwargs):
        raise TimeoutError("old remote acceptance unknown")
    monkeypatch.setattr(f.adapter, "submit", unavailable)
    assert start(f)["submission"]["state"] == "unknown"
    eid = execution(f).object_id
    path = f.runtime.executions.exchange_root / eid / "submission.json"
    value = json.loads(path.read_bytes())
    value.pop("retirement_supported")
    atomic_json(path, value)
    monkeypatch.setattr(f.adapter, "retire_submission", lambda *_: pytest.fail("unsafe retroactive retirement"), raising=False)
    before = f.runtime.executions.budget_accounting(eid)
    with pytest.raises(Exception):
        f.worker.call_tool("worker_experiment_execute", {"action": "cancel"})
    assert f.runtime.executions.budget_accounting(eid) == before
    assert f.runtime.executions.status(eid).state == "authorized"


@pytest.mark.parametrize("command", [False, True])
def test_adapters_require_new_submit_protocol_and_exact_retirement_proof(tmp_path, monkeypatch, command):
    from scidiscovery.artifact_agent.schema.execution import LocalFileDescriptor
    from tcad_artifact.execution_adapter import TCADExecutorAdapter
    from tcad_artifact.command_adapter import CommandTCADExecutorAdapter
    cls = CommandTCADExecutorAdapter if command else TCADExecutorAdapter
    adapter = object.__new__(cls)
    descriptor = LocalFileDescriptor(name="submission", local_path=str(tmp_path / "job.json"), sha256="a"*64,
        size_bytes=0, media_type="application/json")
    names = []
    def old_server(name, payload):
        names.append(name)
        raise RuntimeError("unsupported tool")
    monkeypatch.setattr(adapter, "_call", old_server)
    with pytest.raises(RuntimeError, match="unsupported tool"):
        adapter.submit(descriptor)
    assert names == ["submit_retirable" if command else "tcad_submit_retirable"]
    for invalid in ({"found": False}, {"found": False, "retired": True, "submission_sha256": "b"*64}):
        monkeypatch.setattr(adapter, "_call", lambda *_, value=invalid: value)
        with pytest.raises(RuntimeError, match="retirement"):
            adapter.retire_submission(descriptor)
    monkeypatch.setattr(adapter, "_call", lambda *_: {"found": False, "retired": True, "submission_sha256": descriptor.sha256})
    assert adapter.retire_submission(descriptor) is None


def test_ssh_retirement_maps_exact_remote_identity_to_local_descriptor(tmp_path, monkeypatch):
    from tcad_artifact.ssh_transport import SSHTCADTransport
    transport = SSHTCADTransport(remote=object(), local_result_root=tmp_path)
    local = {"sha256": "a"*64}
    remote = {"sha256": "b"*64}
    monkeypatch.setattr(transport, "_read_marker", lambda value: {"remote_submission": remote})
    calls = []
    def rpc(name, payload):
        calls.append((name, payload))
        if name == "tcad_retire_submission":
            return {"found": False, "retired": True, "submission_sha256": remote["sha256"]}
        return {"run_id": "existing", "state": "running"}
    monkeypatch.setattr(transport, "_rpc", rpc)
    assert transport.handle("submit_retirable", {"submission": local})["run_id"] == "existing"
    assert calls[-1] == ("tcad_submit_retirable", {"submission": remote, "authorization": None})
    assert transport.handle("retire_submission", {"submission": local}) == {
        "found": False, "retired": True, "submission_sha256": local["sha256"]}
    assert calls[-1] == ("tcad_retire_submission", {"submission_sha256": remote["sha256"]})
