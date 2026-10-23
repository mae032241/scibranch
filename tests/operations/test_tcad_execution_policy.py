"""Policy boundaries belong here; solver/installer flows are not duplicated."""
import json

import pytest
from pydantic import ValidationError

from tcad_artifact.execution_policy import ExecutionPolicySnapshot
from tests.operations.tcad_policy_fixtures import policy_fields, policy_snapshot


@pytest.mark.parametrize("storage,wall,outcome", [
    (2_000_000_000, 3600, "policy"),
    (1_999_999_999, 3599, "policy"),
    (2_000_000_001, 3600, "require_human_approval"),
    (2_000_000_000, 3601, "require_human_approval"),
])
def test_inclusive_administrator_allowance(storage, wall, outcome):
    result = policy_snapshot().admission(
        budget={"max_storage_bytes": storage, "wall_time_seconds": wall}, budget_key="a" * 64)
    assert result.outcome == outcome
    assert result.budget == {"max_storage_bytes": storage, "wall_time_seconds": wall}


def test_policy_change_and_disabled_authority_are_explicit():
    original = policy_snapshot()
    fields = policy_fields()
    fields["agent_execution_policy"].update(enabled=False, outside_limits="deny")
    changed = ExecutionPolicySnapshot.model_validate_json(json.dumps(fields), strict=True)
    budget = {"max_storage_bytes": 1024, "wall_time_seconds": 1}
    before = original.admission(budget=budget, budget_key="b" * 64)
    after = changed.admission(budget=budget, budget_key="b" * 64)
    assert before.outcome == "policy" and after.outcome == "deny"
    assert before.policy_digest != after.policy_digest


def test_missing_policy_and_unsupported_transfer_are_rejected():
    fields = policy_fields()
    fields.pop("agent_execution_policy")
    with pytest.raises(ValidationError):
        ExecutionPolicySnapshot.model_validate_json(json.dumps(fields), strict=True)
    fields = policy_fields()
    fields["runner"]["max_transfer_bytes"] = 512
    policy = ExecutionPolicySnapshot.model_validate_json(json.dumps(fields), strict=True)
    with pytest.raises(ValueError, match="transfer capability"):
        policy.admission(budget={"max_storage_bytes": 1024, "wall_time_seconds": 1},
            budget_key="c" * 64)




def test_solver_log_is_counted_and_stdout_collision_preserves_original(tmp_path):
    import hashlib
    from tcad_artifact import remote_runner_py36 as runner
    work = tmp_path / "work"
    work.mkdir()
    genuine = work / "main.log"
    genuine.write_bytes(b"real solver log")
    stdout = tmp_path / "worker.log"
    stdout.write_bytes(b"stdout")
    expected = ({"relative_path": "main.log", "capture": "process_log"},)
    assert runner._logical_storage_bytes(tmp_path, (), expected) >= genuine.stat().st_size + stdout.stat().st_size
    with pytest.raises(RuntimeError, match="conflicts"):
        runner._publish_log_mirror(stdout, genuine, hashlib.sha256(b"stdout").hexdigest(), 6, 1024)
    assert genuine.read_bytes() == b"real solver log"
    assert stdout.read_bytes() == b"stdout"
