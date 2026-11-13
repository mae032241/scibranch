"""An absence observation is insufficient; retirement must fence late submits."""
import json
import threading

import pytest


def test_remote_retirement_fences_submit_still_validating(tmp_path, monkeypatch):
    from tcad_artifact import remote_runner_py36 as runner
    submission_id = "a" * 32
    job = {"request_id": submission_id, "tool_profile": "fixture", "solver_kind": "sprocess", "capability_sha256": "a"*64,
        "input_archive": {"local_path": str(tmp_path / "archive"), "size_bytes": 0}}
    raw = runner._canonical(job)
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
        return 0
    monkeypatch.setattr(runner, "_file_size", validation)
    failures = []
    def submit():
        response = runner._rpc(config, {"request": {"jsonrpc": "2.0", "id": 1,
            "method": "tools/call", "params": {"name": "tcad_submit_retirable", "arguments": {"submission": {}}}}})
        if "error" in response:
            failures.append(response["error"])
    thread = threading.Thread(target=submit)
    thread.start()
    try:
        assert entered.wait(5)
        assert runner._lookup_submission(config, submission_id) == {"found": False}
        retired = runner._retire_submission(config, submission_id)
        assert retired == {"found": False, "retired": True, "submission_id": submission_id}
        assert runner._retire_submission(config, submission_id) == retired
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert len(failures) == 1 and "permanently retired" in str(failures[0])
    assert not (tmp_path / "runs" / ("run_" + submission_id)).exists()


def test_retiring_existing_remote_job_returns_it_without_fencing(tmp_path, monkeypatch):
    from tcad_artifact import remote_runner_py36 as runner
    submission_id = "a" * 32
    config = {"state_root": str(tmp_path / "state"), "result_root": str(tmp_path / "runs")}
    run_id = "run_" + submission_id
    (tmp_path / "runs" / run_id).mkdir(parents=True)
    monkeypatch.setattr(runner, "_status", lambda *_: {"run_id": run_id, "state": "running"})
    assert runner._retire_submission(config, submission_id) == {"found": True, "run_id": run_id, "state": "running"}
    assert not (tmp_path / "state" / "retired_submissions" / submission_id).exists()


def test_local_retirement_survives_service_reopen(tmp_path):
    from tcad_artifact.execution_control import TCADExecutionFacade, TCADExecutionPolicy
    from tests.operations.tcad_policy_fixtures import policy_fields
    fields = {**policy_fields(), "allowed_input_roots": [str(tmp_path)], "tools": [{
        "profile_id": "fixture", "solver_kind": "sprocess", "executable": "/bin/true",
        "public_release_label": "Test fixture", "release_evidence": "Test fixture"}]}
    policy = TCADExecutionPolicy.model_validate_json(json.dumps(fields), strict=True)
    first = TCADExecutionFacade(policy=policy, state_root=tmp_path)
    expected = {"found": False, "retired": True, "submission_id": "a" * 32}
    assert first.tcad_retire_submission(submission_id="a" * 32) == expected
    second = TCADExecutionFacade(policy=policy, state_root=tmp_path)
    assert second.tcad_retire_submission(submission_id="a" * 32) == expected


@pytest.mark.parametrize("command", [False, True])
def test_adapters_require_exact_retirement_proof(tmp_path, monkeypatch, command):
    from scidiscovery.artifact_agent.schema.execution import LocalFileDescriptor
    from tcad_artifact.execution_adapter import TCADExecutorAdapter
    from tcad_artifact.command_adapter import CommandTCADExecutorAdapter
    cls = CommandTCADExecutorAdapter if command else TCADExecutorAdapter
    adapter = object.__new__(cls)
    submission_id = "a" * 32
    path = tmp_path / "job.json"
    raw = json.dumps({"request_id": submission_id}).encode()
    path.write_bytes(raw)
    descriptor = LocalFileDescriptor(name="submission", local_path=str(path),
        size_bytes=len(raw), media_type="application/json")
    for invalid in ({"found": False}, {"found": False, "retired": True, "submission_id": "b" * 32}):
        monkeypatch.setattr(adapter, "_call", lambda *_, value=invalid: value)
        with pytest.raises(RuntimeError, match="retirement"):
            adapter.retire_submission(descriptor)
    monkeypatch.setattr(adapter, "_call", lambda *_: {"found": False, "retired": True, "submission_id": submission_id})
    assert adapter.retire_submission(descriptor) is None
