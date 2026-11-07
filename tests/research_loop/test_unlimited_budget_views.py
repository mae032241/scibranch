"""Unlimited saved facts remain displayable without becoming zero or unknown."""
import pytest

from scidiscovery.research_loop import ui_saved_status, ui_view


def test_unlimited_usage_labels_retain_actual_consumption():
    rows = ui_view.usage_stats(dict(
        budget=dict(max_tokens=None, max_seconds=None, max_steps=80, consumed_seconds=150),
        usage=dict(observed_tokens=3000)), used_steps=2)
    assert rows['tokens']['limit'] == '不限'
    assert rows['tokens']['raw'] == 3000
    assert rows['time']['limit'] == '不限'
    assert rows['time']['raw'] == 150
    assert rows['tokens']['over'] is False and rows['time']['over'] is False
    missing = ui_view.usage_stats({})
    assert missing['tokens']['limit'] == missing['time']['limit'] == '未记录'


@pytest.mark.parametrize('seconds, expected', [(None, None), (120, 120)])
def test_saved_budget_without_started_clock_handles_optional_limit(tmp_path, seconds, expected):
    view = ui_saved_status._budget(tmp_path,
        dict(max_tokens=None, max_seconds=seconds, max_steps=80, time_budget_basis='active'), [])
    assert view['seconds_remaining'] == expected
    assert view['consumed_seconds'] == 0
