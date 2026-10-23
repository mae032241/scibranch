"""Single-request admission, cumulative reservations and actual terminal usage."""
import pytest

from tests.operations.tcad_policy_fixtures import policy_snapshot


def test_config_separates_one_hour_request_from_six_hour_pool():
    policy = policy_snapshot()
    admission = policy.admission(budget={'wall_time_seconds': 3600, 'max_storage_bytes': 100}, budget_key='a'*64)
    assert admission.outcome == 'policy'
    assert admission.per_execution_allowance['wall_time_seconds'] == 3600
    assert admission.allowance['wall_time_seconds'] == 21600
    assert policy.admission(budget={'wall_time_seconds': 3601, 'max_storage_bytes': 100},
        budget_key='a'*64).outcome == 'require_human_approval'


@pytest.mark.parametrize('manifest,expected', [
    ({'resource_usage': {'elapsed_seconds': 2.1}}, {'wall_time_seconds': 3}),
    ({'started_at': '2026-09-30T00:00:00Z', 'completed_at': '2026-09-30T00:00:07Z'}, {'wall_time_seconds': 7}),
    ({'resource_usage': {'elapsed_seconds': True, 'observed_storage_high_water_bytes': False}}, None),
    ({}, None),
])
def test_runner_and_local_control_report_only_observed_dimensions(manifest, expected):
    from tcad_artifact.remote_runner_py36 import _consumed_budget as remote
    from tcad_artifact.execution_control import _consumed_budget as local
    assert remote(manifest) == local(manifest) == expected
