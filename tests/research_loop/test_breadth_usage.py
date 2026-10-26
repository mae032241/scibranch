"""Strict advisory samples; authored fake terminal meters are mechanical fixtures."""
import copy
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c, execution, fork_runtime, native, ui_saved_status


def sample(tmp_path,monkeypatch):
    root=tmp_path/'instance';(root/'jobs').mkdir(parents=True)
    records={}
    for step in range(1,6):
        c.write(root/'jobs'/f'{step:03d}.json',dict(step=step))
        record=root/'native-evidence'/f'{step:03d}';record.mkdir(parents=True);records[step]=[record]
        c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json')))
        c.write(record/'usage.json',dict(input_tokens=8,cached_input_tokens=5,output_tokens=2))
        c.write(record/'native-outcome.json',dict(returncode=0,native_started=False,
            metering=dict(coverage='terminal',reporting_delay_unmeasured=False,inflight_usage_unknown=False)))
    monkeypatch.setattr(fork_runtime,'local_steps',lambda *args,**kwargs:list(records))
    monkeypatch.setattr(execution,'records',lambda root,step:records[step])
    monkeypatch.setattr(execution,'origin',lambda root,step:records[step][0])
    state=dict(portfolio=dict(cycles=[dict(w_step=2,a_steps=[3]),dict(w_step=4,a_steps=[5])]))
    return root,state,records


def advice(root,state,**kwargs):
    return native.cycle_token_advice(root,state,usage_snapshot=dict(observed_tokens=50,accounted_tokens=50,unknown_steps=[]),
        budget_snapshot=dict(max_tokens=250),**kwargs)


def test_two_complete_local_cycles_count_cached_once_and_amendment_changes_balance(tmp_path,monkeypatch):
    root,state,records=sample(tmp_path,monkeypatch)
    result=advice(root,state)
    assert result['status']=='available' and result['mean_reported_tokens']==20 and result['reference_cycles']==10
    later=records[3][0]/'repair';later.mkdir()
    for name in ('native-intent.json','native-outcome.json','usage.json'):
        c.write(later/name,c.read(records[3][0]/name))
    records[3].append(later)
    result=advice(root,state)
    assert result['complete_sample_count']==2 and result['mean_reported_tokens']==25
    assert result['samples'][0]['reported_tokens']==30  # both accepted A attempts, no duplicate cached input
    amended=native.cycle_token_advice(root,state,usage_snapshot=dict(observed_tokens=60,unknown_steps=[]),budget_snapshot=dict(max_tokens=560))
    assert amended['reference_cycles']==20
    assert len(state['portfolio']['cycles'])==2


@pytest.mark.parametrize('damage',['missing','unknown','lag','inflight','one','zero'])
def test_incomplete_or_insufficient_samples_never_fill_missing_usage_with_zero(tmp_path,monkeypatch,damage):
    root,state,records=sample(tmp_path,monkeypatch)
    if damage=='missing':(records[3][0]/'usage.json').unlink()
    elif damage=='unknown':c.write(records[3][0]/'native-outcome.json',dict(returncode=0,metering=dict(coverage='unknown')))
    elif damage=='lag':c.write(records[3][0]/'native-outcome.json',dict(returncode=0,metering=dict(coverage='terminal')))
    elif damage=='inflight':(records[3][0]/'native-outcome.json').unlink()
    elif damage=='one':state['portfolio']['cycles'].pop()
    elif damage=='zero':
        for row in records.values():c.write(row[0]/'usage.json',dict(input_tokens=0,cached_input_tokens=0,output_tokens=0))
    result=advice(root,state)
    assert result['status']=='unavailable' and result['reference_cycles'] is None and result['reasons']
    if damage in {'missing','unknown','lag','inflight'}:assert result['complete_sample_count']==1 and result['excluded_sample_count']==1


def test_repair_and_author_correction_are_excluded_from_token_samples(tmp_path,monkeypatch):
    root,state,_=sample(tmp_path,monkeypatch)
    for cycle,reason in zip(state['portfolio']['cycles'],('repair_cycle_consumption_incomplete','author_correction_cycle_consumption_incomplete')):
        cycle['sample_exclusion']=reason
    result=advice(root,state)
    assert result['complete_sample_count']==0 and result['excluded_sample_count']==2
    assert {row['reason'] for row in result['excluded_samples']}=={'repair_cycle_consumption_incomplete','author_correction_cycle_consumption_incomplete'}


def test_inherited_cycles_do_not_charge_parent_tokens_to_child_sample(tmp_path,monkeypatch):
    root,state,records=sample(tmp_path,monkeypatch)
    state['portfolio']['cycles'].insert(0,dict(w_step=98,a_steps=[99]))
    result=advice(root,state)
    assert result['complete_sample_count']==2 and result['remaining_reported_tokens']==200
    assert all(row['w_step']<98 for row in result['samples'])


def test_saved_metrics_keep_partial_and_invalidate_new_inflight_or_balance(tmp_path,monkeypatch):
    root,state,records=sample(tmp_path,monkeypatch)
    snapshot=dict(source_step=5,partial=True,portfolio=dict(completed_cycle_count=2),token_advice=advice(root,state))
    c.write(root/'host-history/overview.json',dict(portfolio_metrics=snapshot))
    (records[5][0]/'result-identity.json').write_text('{}')
    monkeypatch.setattr(ui_saved_status,'attempts',lambda root,step:records[step])
    result=ui_saved_status.portfolio_metrics(root,[5],dict(accounted_tokens=50,unknown_steps=[]),dict(max_tokens=250))
    assert result['partial'] and result['token_advice']['status']=='unavailable'
    snapshot['partial']=False;c.write(root/'host-history/overview.json',dict(portfolio_metrics=snapshot))
    (records[4][0]/'native-outcome.json').unlink()
    result=ui_saved_status.portfolio_metrics(root,[4,5],dict(accounted_tokens=50,unknown_steps=[]),dict(max_tokens=300))
    assert result['token_advice']['reference_cycles'] is None
    assert {'current_usage_inflight','current_balance_changed'}<=set(result['token_advice']['reasons'])


def test_saved_complete_samples_recompute_after_ceiling_amendment_only(tmp_path,monkeypatch):
    root,state,records=sample(tmp_path,monkeypatch)
    c.write(root/'host-history/overview.json',dict(portfolio_metrics=dict(source_step=5,partial=False,
        portfolio=dict(completed_cycle_count=2),token_advice=advice(root,state))))
    (records[5][0]/'result-identity.json').write_text('{}')
    monkeypatch.setattr(ui_saved_status,'attempts',lambda root,step:records[step])
    result=ui_saved_status.portfolio_metrics(root,list(records),dict(accounted_tokens=50,unknown_steps=[]),dict(max_tokens=450))
    assert result['token_advice']['status']=='available' and result['token_advice']['reference_cycles']==20
    assert result['portfolio']['completed_cycle_count']==2
    # A new accepted request invalidates this snapshot even if no new tokens
    # have reached the saved meter yet.
    new=records[5][0]/'new-attempt';new.mkdir();(new/'native-intent.json').write_text('{}');records[5].append(new)
    result=ui_saved_status.portfolio_metrics(root,list(records),dict(accounted_tokens=50,unknown_steps=[]),dict(max_tokens=450))
    assert result['token_advice']['status']=='unavailable' and result['token_advice']['reference_cycles'] is None


def test_corrupt_disposable_metrics_cache_is_unavailable(tmp_path):
    root=tmp_path/'instance';(root/'host-history').mkdir(parents=True)
    (root/'host-history/overview.json').write_text('{invalid')
    result=ui_saved_status.portfolio_metrics(root,[],dict(accounted_tokens=0),dict(max_tokens=100))
    assert result['status']=='unavailable' and result['partial'] and result['source_step'] is None
