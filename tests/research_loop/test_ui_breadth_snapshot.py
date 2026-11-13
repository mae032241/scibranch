"""Saved UI arithmetic, without replaying or qualifying scientific originals."""
import copy
import os
from pathlib import Path

import pytest

from scidiscovery.research_loop import accepted_budget, accepted_search, contracts as c
from scidiscovery.research_loop import ui_components, ui_saved_status, ui_view


HEAD = 'host-history/accepted-budget/head.json'
USAGE = dict(accounted_tokens=50, unknown_steps=[])
BUDGET = dict(max_tokens=250)


def head_snapshot(*, complete=0, excluded=1):
    """Mechanical display data in the current producer's aggregate shape.

    These counts exercise presentation only; they assert no scientific verdict,
    successful call, complete meter or execution permission.
    """
    summary = accepted_search.empty()
    cycles = complete + excluded
    summary['table']['mechanisms'] = {f'h001.m{index}': dict(label=label,
        participated_cycles=cycles if index in (1, 3) else 0)
        for index, label in enumerate(('Alpha', 'Beta', 'Gamma', 'Delta'), 1)}
    summary['portfolio'].update(completed_cycle_count=cycles,
        pending_w=dict(step=5, branches=['h001.m2'], branch='h001.m2', completed=True, cycle_root=5),
        main_line='h001.m3', mainline_streak=1,
        mainline_source=dict(source_step=3, pointer='/portfolio_review/main_line'),
        token_advice=dict(complete_sample_count=complete, excluded_sample_count=excluded,
            reported_tokens_sum=20 * complete,
            samples=[dict(w_step=2 + 2 * index, a_step=3 + 2 * index, reported_tokens=20)
                for index in range(min(complete, 2))]))
    return dict(version=2, prefix=dict(cutoff=5), dispatch_count=5,
        current_native=None, search=summary)


def rendered(value, *, usage=None):
    view = ui_view.breadth_metrics(dict(portfolio_metrics=value, usage=usage or USAGE),
        lambda text, limit=16384: str(text)[:limit])
    return view, ui_components.breadth_metrics(view)


def forbidden(*args, **kwargs):
    pytest.fail('A UI consumer attempted to read, discover or qualify originals')


def test_current_head_displays_participation_mainline_and_pending_without_overview(tmp_path):
    c.write(tmp_path / HEAD, head_snapshot())
    c.write(tmp_path / 'host-history/overview.json', dict(candidate_count=0, node_count=5))
    value = ui_saved_status.portfolio_metrics(tmp_path, [], USAGE, BUDGET)
    view, markup = rendered(value)
    assert view['completed_cycle_count'] == 1
    assert [row['fraction_text'] for row in view['participation']] == ['100%', '0%', '100%', '0%']
    assert view['pending_step'] == 5 and view['pending_labels'] == ['Beta']
    assert view['main_line'] == 'Gamma' and view['mainline_step'] == 3
    assert '待 A 评估的 W' in markup and '/nodes/5/science' in markup
    assert 'Gamma' in markup and '未计入已完成周期' in markup
    assert '完整统计快照暂不可用' not in markup


def test_adapter_and_formatters_use_values_without_lazy_or_filesystem_reads(monkeypatch):
    head = accepted_budget.AcceptedState(Path('/unused-ui-instance'), head_snapshot(), base={})
    before = copy.deepcopy(dict(head))
    with monkeypatch.context() as bounded:
        bounded.setattr(c, 'read', forbidden)
        bounded.setattr(c, 'settings', forbidden)
        bounded.setattr(accepted_budget, 'read', forbidden)
        bounded.setattr(accepted_budget.AcceptedState, 'row', forbidden)
        for name in ('read_text', 'read_bytes', 'stat', 'lstat', 'exists', 'iterdir', 'glob'):
            bounded.setattr(Path, name, forbidden)
        bounded.setattr(os, 'scandir', forbidden)
        value = ui_saved_status.portfolio_metrics_from_head(head, USAGE, BUDGET)
        view, markup = rendered(value)
    assert view['available'] and '已完成独立 W→A 评估周期' in markup
    assert dict(head) == before


def test_loader_reads_exact_head_once_and_ignores_historical_numbers(tmp_path, monkeypatch):
    path = tmp_path / HEAD
    c.write(path, head_snapshot())
    for step in range(1, 41):
        c.write(tmp_path / 'jobs' / f'{step:03d}.json', dict(unrelated=True))
        c.write(tmp_path / 'native-evidence' / f'{step:03d}' / 'result-identity.json', dict(unrelated=True))
        c.write(tmp_path / 'control-ledger' / f'{step:06d}.json', dict(unrelated=True))
    read, stat = c.read, Path.stat
    reads = []
    def read_head(actual):
        assert actual == path
        reads.append(actual)
        return read(actual)
    def head_stat(actual, *args, **kwargs):
        assert actual == path
        return stat(actual, *args, **kwargs)
    class HistoricalNumbers:
        def __iter__(self):
            forbidden()
    with monkeypatch.context() as bounded:
        bounded.setattr(c, 'read', read_head)
        bounded.setattr(c, 'settings', forbidden)
        bounded.setattr(accepted_budget, 'read', forbidden)
        bounded.setattr(ui_saved_status, 'attempts', forbidden)
        bounded.setattr(Path, 'stat', head_stat)
        bounded.setattr(Path, 'glob', forbidden)
        bounded.setattr(Path, 'iterdir', forbidden)
        bounded.setattr(os, 'scandir', forbidden)
        value = ui_saved_status.portfolio_metrics(tmp_path, HistoricalNumbers(), USAGE, BUDGET, memo={})
    assert value['portfolio']['completed_cycle_count'] == 1 and reads == [path]


@pytest.mark.parametrize('damage,reason', [
    ('unknown', 'usage_unknown'), ('reserved_unknown', 'usage_unknown'),
    ('inflight', 'current_usage_inflight'), ('one_sample', 'insufficient_complete_local_cycles'),
    ('no_samples', 'insufficient_complete_local_cycles'), ('balance', 'current_balance_unavailable'),
    ('zero_mean', 'nonpositive_sample_mean')])
def test_unavailable_estimate_preserves_accepted_statistics(damage, reason):
    head, usage = head_snapshot(complete=2), copy.deepcopy(USAGE)
    if damage == 'unknown':
        usage['unknown_steps'] = [5]
    elif damage == 'reserved_unknown':
        usage['reserved_unknown_steps'] = [5]
    elif damage == 'inflight':
        head['current_native'] = dict(status='open', step=6)
    elif damage in {'one_sample', 'no_samples'}:
        head['search']['portfolio']['token_advice']['complete_sample_count'] = int(damage == 'one_sample')
    elif damage == 'balance':
        usage.pop('accounted_tokens')
    elif damage == 'zero_mean':
        head['search']['portfolio']['token_advice']['reported_tokens_sum'] = 0
    view, markup = rendered(ui_saved_status.portfolio_metrics_from_head(head, usage, BUDGET), usage=usage)
    assert view['available'] and view['completed_cycle_count'] == 3
    assert view['pending_step'] == 5 and view['main_line'] == 'Gamma'
    assert not view['token_advice']['available'] and reason in view['token_advice']['reasons']
    assert '已评估 3 周期' in markup and '按已报告 token 的参考轮数：不可估' in markup


@pytest.mark.parametrize('advice', [None, {}, dict(complete_sample_count='unknown')])
def test_missing_optional_advice_does_not_hide_statistics(advice):
    head = head_snapshot()
    head['search']['portfolio']['token_advice'] = advice
    view, markup = rendered(ui_saved_status.portfolio_metrics_from_head(head, USAGE, BUDGET))
    assert view['available'] and view['completed_cycle_count'] == 1
    assert not view['token_advice']['available'] and '已评估 1 周期' in markup


@pytest.mark.parametrize('damage', ['missing', 'malformed'])
def test_unusable_optional_samples_keep_known_counts_and_mainline(damage):
    head = head_snapshot(complete=2)
    portfolio = head['search']['portfolio']
    if damage == 'missing':
        portfolio['token_advice'].pop('samples')
    else:
        portfolio['token_advice']['samples'] = [{}]
    portfolio['mainline_source'] = 'unrecorded'
    view, markup = rendered(ui_saved_status.portfolio_metrics_from_head(head, USAGE, BUDGET))
    assert view['available'] and view['completed_cycle_count'] == 3
    assert view['main_line'] == 'Gamma' and view['mainline_step'] is None
    assert not view['token_advice']['available']
    assert view['token_advice']['complete_sample_count'] == 2
    assert view['token_advice']['excluded_sample_count'] == 1
    assert '完整本分支周期样本：2' in markup and '排除样本：1' in markup


def test_complete_saved_samples_use_current_balance_without_reopening_samples():
    head = head_snapshot(complete=2)
    value = ui_saved_status.portfolio_metrics_from_head(head, USAGE, BUDGET)
    assert value['token_advice']['status'] == 'available'
    assert value['token_advice']['reference_cycles'] == 10
    amended = ui_saved_status.portfolio_metrics_from_head(head, USAGE, dict(max_tokens=450))
    assert amended['token_advice']['reference_cycles'] == 20
    assert amended['token_advice']['accepted_attempt_count'] == 5
    assert 'H/E/audit/external calls' in amended['token_advice']['scope']


def test_active_work_uses_saved_global_total_and_truthful_omissions():
    head = head_snapshot()
    head['search']['work_count'] = 4
    head['search']['current_facts'] = {
        'search/work/A003-D001': dict(work_ref='A003-D001', source_a=3,
            question='Which retained observation changes the decision?', change_criterion='A contradictory original.',
            status='deferred', updates=[dict(source_a=3, action='defer', reason='Required input is missing.')]),
        'search/work/A002-D001': dict(status='withdrawn')}
    value = ui_saved_status.portfolio_metrics_from_head(head, USAGE, BUDGET)
    view, markup = rendered(value)
    assert value['active_work_count'] == view['active_work_count'] == 4
    assert len(view['work']) == 1 and view['work_omitted'] == 3
    assert '仍有效的判别工作（4）' in markup and '另有 3 项未展开' in markup
    assert '/nodes/3/science' in markup and '/results?after=0' in markup


@pytest.mark.parametrize('contents', [None, '{invalid', '[]', '{}', '{"version":2,"search":null}'])
def test_missing_or_corrupt_head_is_unavailable_without_legacy_fallback(tmp_path, monkeypatch, contents):
    c.write(tmp_path / 'host-history/overview.json', dict(portfolio_metrics=dict(portfolio=dict(completed_cycle_count=99))))
    if contents is not None:
        path = tmp_path / HEAD
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    monkeypatch.setattr(c, 'settings', forbidden)
    monkeypatch.setattr(accepted_budget, 'read', forbidden)
    monkeypatch.setattr(ui_saved_status, 'attempts', forbidden)
    value = ui_saved_status.portfolio_metrics(tmp_path, [], USAGE, BUDGET)
    assert value['status'] == 'unavailable' and value['partial'] and value['source_step'] is None
    assert not rendered(value)[0]['available']
