"""Mixed-version admission and explicit administrator recovery, no live solver."""
import fcntl
import json
from types import SimpleNamespace

import pytest


def test_busy_remote_retirement_returns_actionable_error(tmp_path):
    from tcad_artifact import remote_runner_py36 as runner
    config = {'state_root': str(tmp_path), 'result_root': str(tmp_path / 'runs')}
    with (tmp_path / 'submission.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        with pytest.raises(RuntimeError, match='retry abandon/cancel later'):
            runner._retire_submission(config, 'a'*64)
    assert not (tmp_path / 'retired_submissions').exists()


@pytest.mark.process_e2e
def test_deployment_rejects_old_runner_even_with_capabilities_and_policy(tmp_path):
    from tests.artifact_agent.test_remote_tcad_deployment import setup, SOURCE
    module, request, runner, config = setup(tmp_path)
    source = SOURCE.read_text().replace('if name == "tcad_submission_protocol":', 'if name == "unsupported_protocol":')
    runner.write_text(source)
    with pytest.raises(RuntimeError, match='protocol discovery'):
        module.probe(runner, config)
    runner.write_bytes(SOURCE.read_bytes())
    assert module.probe(runner, config)['policy_available'] is True
