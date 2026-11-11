"""Accepted-token advice and current balances; no native process runs."""
import copy

import pytest

from scidiscovery.research_loop import accepted_budget, accepted_search, contracts as c, execution, native, ui_saved_status


@pytest.fixture
def accepted_advice(monkeypatch):
    head=dict(dispatch_count=5,search=accepted_search.empty())
    head['search']['portfolio']['token_advice']=dict(complete_sample_count=2,excluded_sample_count=1,
        reported_tokens_sum=40,samples=[dict(w_step=2,a_step=3,reported_tokens=20),
            dict(w_step=4,a_step=5,reported_tokens=20)])
    monkeypatch.setattr(accepted_budget,'read',lambda root:head)
    def forbidden(*args,**kwargs):
        pytest.fail('Accepted cycle advice must not reopen native originals')
    monkeypatch.setattr(c,'read',forbidden)
    monkeypatch.setattr(execution,'records',forbidden)
    return head


def test_accepted_samples_recompute_current_balance_without_reopening_history(accepted_advice):
    head=accepted_advice;before=copy.deepcopy(head)
    # Legacy caller-supplied cycle locations cannot import parent costs.
    state=dict(portfolio=dict(cycles=[dict(w_step=98,a_steps=[99])]))
    result=native.cycle_token_advice(None,state,usage_snapshot=dict(accounted_tokens=50,unknown_steps=[]),budget_snapshot=dict(max_tokens=250))
    assert result['mean_reported_tokens']==20 and result['reference_cycles']==10
    assert result['excluded_sample_count']==1 and result['accepted_attempt_count']==5
    amended=native.cycle_token_advice(None,state,usage_snapshot=dict(accounted_tokens=60,unknown_steps=[]),budget_snapshot=dict(max_tokens=560))
    assert amended['reference_cycles']==25
    assert head==before and all(row['w_step']<98 for row in result['samples'])


@pytest.mark.parametrize('damage,reason', [('one','insufficient_complete_local_cycles'),
    ('zero','nonpositive_sample_mean'), ('unknown','usage_unknown'), ('balance','current_balance_unavailable')])
def test_unavailable_accepted_advice_never_invents_a_cycle_reference(accepted_advice,damage,reason):
    advice=accepted_advice['search']['portfolio']['token_advice']
    usage=dict(accounted_tokens=50,unknown_steps=[])
    if damage=='one':advice['complete_sample_count']=1
    elif damage=='zero':advice['reported_tokens_sum']=0
    elif damage=='unknown':usage['unknown_steps']=[6]
    else:usage['accounted_tokens']=None
    result=native.cycle_token_advice(None,{},usage_snapshot=usage,budget_snapshot=dict(max_tokens=250))
    assert result['status']=='unavailable' and result['reference_cycles'] is None
    assert reason in result['reasons']


def test_corrected_cycle_exclusion_updates_accepted_sum_once(accepted_advice):
    summary=accepted_advice['search']
    summary['current_facts'][accepted_search.key('cycle',2)]=dict(w_step=2,sample_complete=True,reported_tokens=20)
    delta=accepted_search._Delta(summary,lambda key:None)
    accepted_search._exclude_cycle(delta,2)
    accepted_search._exclude_cycle(delta,2)
    advice=delta.summary['portfolio']['token_advice']
    assert advice['complete_sample_count']==1 and advice['excluded_sample_count']==2
    assert advice['reported_tokens_sum']==20 and [row['w_step'] for row in advice['samples']]==[4]


def test_saved_metrics_keep_counts_and_invalidate_new_inflight_or_unknown_usage(tmp_path):
    from test_ui_breadth_snapshot import HEAD, head_snapshot
    head=head_snapshot(complete=2)
    c.write(tmp_path/HEAD,head)
    result=ui_saved_status.portfolio_metrics(tmp_path,[5],dict(accounted_tokens=50,unknown_steps=[5]),dict(max_tokens=250))
    assert not result['partial'] and result['portfolio']['completed_cycle_count']==3
    assert result['token_advice']['status']=='unavailable' and 'usage_unknown' in result['token_advice']['reasons']
    head['current_native']=dict(status='open',step=6)
    c.write(tmp_path/HEAD,head)
    result=ui_saved_status.portfolio_metrics(tmp_path,[4,5],dict(accounted_tokens=50,unknown_steps=[]),dict(max_tokens=300))
    assert result['portfolio']['completed_cycle_count']==3 and result['token_advice']['reference_cycles'] is None
    assert 'current_usage_inflight' in result['token_advice']['reasons']


def test_saved_complete_samples_recompute_after_ceiling_amendment_only(tmp_path):
    from test_ui_breadth_snapshot import HEAD, head_snapshot
    head=head_snapshot(complete=2)
    c.write(tmp_path/HEAD,head)
    result=ui_saved_status.portfolio_metrics(tmp_path,[5],dict(accounted_tokens=50,unknown_steps=[]),dict(max_tokens=450))
    assert result['token_advice']['status']=='available' and result['token_advice']['reference_cycles']==20
    assert result['portfolio']['completed_cycle_count']==3
    # A new accepted request invalidates this snapshot even if no new tokens
    # have reached the saved meter yet.
    head['dispatch_count']+=1;head['current_native']=dict(status='open',step=6)
    c.write(tmp_path/HEAD,head)
    result=ui_saved_status.portfolio_metrics(tmp_path,[5],dict(accounted_tokens=50,unknown_steps=[]),dict(max_tokens=450))
    assert result['token_advice']['status']=='unavailable' and result['token_advice']['reference_cycles'] is None


def test_corrupt_disposable_metrics_cache_is_unavailable(tmp_path):
    from test_ui_breadth_snapshot import HEAD
    root=tmp_path/'instance';(root/HEAD).parent.mkdir(parents=True)
    (root/HEAD).write_text('{invalid')
    result=ui_saved_status.portfolio_metrics(root,[],dict(accounted_tokens=0),dict(max_tokens=100))
    assert result['status']=='unavailable' and result['partial'] and result['source_step'] is None
