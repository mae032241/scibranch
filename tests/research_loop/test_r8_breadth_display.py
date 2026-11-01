"""Current accepted-summary versions retain exploration metrics in the UI."""
import copy
import os
from pathlib import Path

import pytest

from scidiscovery.research_loop import accepted_budget, accepted_search, contracts as c
from scidiscovery.research_loop import ui_components, ui_saved_status, ui_view


HEAD = 'host-history/accepted-budget/head.json'
USAGE = dict(accounted_tokens=50, unknown_steps=[])
BUDGET = dict(max_tokens=250)


def display_head(*, complete=0, excluded=2):
    """Synthetic display data matching the r8 aggregate counts, not a verdict."""
    summary = accepted_search.empty()
    refs = [f'h001.m{number}' for number in range(1, 7)]
    summary['mechanism_count'] = len(refs)
    summary['table']['ordinals'] = refs
    summary['table']['mechanisms'] = {ref: dict(label=f'Declared mechanism {number}',
        participated_cycles=participated) for number, (ref, participated) in enumerate(
            zip(refs, (1, 2, 2, 1, 0, 0)), 1)}
    summary['portfolio'].update(completed_cycle_count=2, main_line=refs[1],
        mainline_streak=3, mainline_source=dict(source_step=8, pointer='/portfolio_review/main_line'),
        token_advice=dict(complete_sample_count=complete, excluded_sample_count=excluded,
            reported_tokens_sum=20 * complete,
            samples=[dict(w_step=w_step, a_step=a_step, reported_tokens=20)
                for w_step, a_step in ((2, 3), (6, 8))[:complete]]))
    return dict(version=2, prefix=dict(cutoff=8), dispatch_count=8,
        current_native=None, search=summary)


def rendered(metrics, usage=USAGE):
    clean = lambda value, limit=16384: str(value)[:limit]
    view = ui_view.breadth_metrics(dict(portfolio_metrics=metrics, usage=usage), clean)
    return view, ui_components.breadth_metrics(view)


def forbidden(*args, **options):
    pytest.fail('The exploration display attempted an unrelated read, traversal or mutation')


def test_current_producer_summary_renders_r8_cycles_participation_and_exclusions_without_reads(monkeypatch):
    head = display_head()
    assert head['search']['version'] == accepted_search.VERSION
    before = copy.deepcopy(head)
    with monkeypatch.context() as bounded:
        bounded.setattr(c, 'read', forbidden)
        bounded.setattr(c, 'settings', forbidden)
        bounded.setattr(c, 'write', forbidden)
        bounded.setattr(accepted_budget, 'read', forbidden)
        bounded.setattr(accepted_budget, 'load', forbidden)
        bounded.setattr(accepted_budget.AcceptedState, 'row', forbidden)
        for name in ('open', 'read_text', 'read_bytes', 'stat', 'lstat', 'exists', 'iterdir', 'glob'):
            bounded.setattr(Path, name, forbidden)
        bounded.setattr(os, 'scandir', forbidden)
        metrics = ui_saved_status.portfolio_metrics_from_head(head, USAGE, BUDGET)
        view, markup = rendered(metrics)
    assert head == before
    assert view['available'] and not view['partial'] and view['source_step'] == 8
    assert view['completed_cycle_count'] == 2
    assert [row['participated_cycles'] for row in view['participation']] == [1, 2, 2, 1, 0, 0]
    assert [row['fraction_text'] for row in view['participation']] == ['50%', '100%', '100%', '50%', '0%', '0%']
    assert view['main_line'] == 'Declared mechanism 2' and view['mainline_streak'] == 3
    assert '探索投入提示 · 已评估 2 周期' in markup
    assert '完整本分支周期样本：0' in markup and '排除样本：2' in markup
    assert '按已报告 token 的参考轮数：不可估' in markup
    assert '完整统计快照暂不可用' not in markup


@pytest.mark.parametrize('reason', ['usage_unknown', 'current_usage_inflight'])
def test_unknown_or_inflight_estimate_preserves_current_exploration_statistics(reason):
    head, usage = display_head(complete=2, excluded=0), copy.deepcopy(USAGE)
    if reason == 'usage_unknown':
        usage['unknown_steps'] = [8]
    else:
        head['current_native'] = dict(step=9, status='open')
    metrics = ui_saved_status.portfolio_metrics_from_head(head, usage, BUDGET)
    view, markup = rendered(metrics, usage)
    assert view['available'] and not view['partial'] and view['completed_cycle_count'] == 2
    assert len(view['participation']) == 6 and view['mainline_streak'] == 3
    assert not view['token_advice']['available'] and reason in view['token_advice']['reasons']
    assert '已评估 2 周期' in markup and '完整本分支周期样本：2' in markup
    assert '按已报告 token 的参考轮数：不可估' in markup
    assert '完整统计快照暂不可用' not in markup


@pytest.mark.parametrize('version', [1, True, None, '2', accepted_search.VERSION + 1])
def test_stale_or_unsupported_summary_version_remains_unavailable(version):
    head = display_head()
    head['search']['version'] = version
    metrics = ui_saved_status.portfolio_metrics_from_head(head, USAGE, BUDGET)
    view, markup = rendered(metrics)
    assert metrics['status'] == 'unavailable' and metrics['partial']
    assert not view['available'] and '完整统计快照暂不可用' in markup


def test_current_summary_loader_reads_one_exact_head_without_legacy_or_history_fallback(tmp_path, monkeypatch):
    path = tmp_path / HEAD
    c.write(path, display_head())
    c.write(tmp_path / 'host-history/overview.json', dict(
        portfolio_metrics=dict(portfolio=dict(completed_cycle_count=999))))
    original_read = c.read
    reads = []

    def read_head(actual):
        assert actual == path
        reads.append(actual)
        return original_read(actual)

    class HistoricalNumbers:
        def __iter__(self):
            forbidden()

    with monkeypatch.context() as bounded:
        bounded.setattr(c, 'read', read_head)
        bounded.setattr(c, 'settings', forbidden)
        bounded.setattr(c, 'write', forbidden)
        bounded.setattr(accepted_budget, 'read', forbidden)
        bounded.setattr(accepted_budget, 'load', forbidden)
        bounded.setattr(ui_saved_status, 'attempts', forbidden)
        bounded.setattr(Path, 'iterdir', forbidden)
        bounded.setattr(Path, 'glob', forbidden)
        bounded.setattr(os, 'scandir', forbidden)
        metrics = ui_saved_status.portfolio_metrics(tmp_path, HistoricalNumbers(), USAGE, BUDGET, memo={})
        view, markup = rendered(metrics)
    assert reads == [path] and view['completed_cycle_count'] == 2
    assert '已评估 2 周期' in markup and '999' not in markup
