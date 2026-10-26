"""FINAL authored declarations at real seal/replay gates; no science verdict."""
import copy

import pytest

from scidiscovery.research_loop import candidate_nominations as nominations, contracts as c, execution, history, search
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_history_handoff import fixture_final_coverage
from test_portfolio_contract import prepared, raw_author, comparison, rewrite

pytestmark = pytest.mark.process_e2e


@pytest.mark.parametrize('damage',['missing','null','malformed','omitted','duplicate','unknown','blank_reason','missing_questions','private_ref','unknown_ref'])
def test_final_coverage_raw_seal_gate_rejects_missing_or_unbound_declarations(tmp_path,damage):
    root=prepared(tmp_path);job,payload,record=raw_author(root,review=comparison())
    value=payload['final_coverage']
    if damage=='missing':payload.pop('final_coverage')
    elif damage=='null':payload['final_coverage']=None
    elif damage=='malformed':payload['final_coverage']={}
    elif damage=='omitted':value.pop()
    elif damage=='duplicate':value.append(copy.deepcopy(value[0]))
    elif damage=='unknown':value[0]['mechanism_ref']='h999.m1'
    elif damage=='blank_reason':value[0]['reason']=' \t'
    elif damage=='missing_questions':value[0].pop('unanswered_questions')
    elif damage=='private_ref':value[0]['science_refs']=[dict(source_step=1,pointer='/direct_refs/0/path')]
    elif damage=='unknown_ref':value[0]['science_refs']=[dict(source_step=99,pointer='/scientific_result')]
    rewrite(record,payload)
    with pytest.raises(OutputRepairRequired,match='final_coverage'):c.seal_result(root,3)
    assert not (record/'result-identity.json').exists() and not (record/'result.json').exists()


def test_sealed_final_coverage_retains_authored_depth_questions_and_replays(tmp_path):
    root=prepared(tmp_path);job,payload,record=raw_author(root,review=comparison())
    payload['final_coverage'][0].update(status='deep',reason='Synthetic A declares a bounded deep test; adequacy remains unqualified.',
        unanswered_questions=['Does the proxy transfer to the target condition?'])
    payload['final_coverage'][1].update(status='screened_pending',reason='Synthetic A retains a screened comparison for later evidence.')
    rewrite(record,payload);c.seal_result(root,3)
    assert c.validate(c.workspace(root,3))['final_coverage']==payload['final_coverage']
    assert c.science_view(job,root)['final_coverage_mechanisms']==job['final_coverage_mechanisms']
    saved=c.read(root/'host-history/overview.json')['portfolio_metrics']
    assert saved['source_step']==3 and saved['portfolio']['completed_cycle_count']==1
    assert saved['portfolio']['mainline_streak']==1 and saved['token_advice']['status']=='unavailable'


def test_waived_mechanism_still_needs_authored_final_row(tmp_path):
    root=prepared(tmp_path);job,payload,record=raw_author(root,review=comparison())
    payload['search_review']['dispositions']=[dict(mechanism_ref='h001.m2',status='waived',reason='Synthetic A rejects this explanation under exact constraints.')]
    rewrite(record,payload);c.seal_result(root,3)
    next_job=c.build_job(root,4,'A',[1,3])
    assert next(row for row in next_job['final_coverage_mechanisms'] if row['mechanism_ref']=='h001.m2')['status']=='waived'
    _,value,next_record=raw_author(root,4,(1,3),review=None)
    value['final_coverage']=[row for row in value['final_coverage'] if row['mechanism_ref']!='h001.m2']
    rewrite(next_record,value)
    with pytest.raises(OutputRepairRequired,match='final_coverage'):c.seal_result(root,4)


@pytest.mark.parametrize('entry',['ordinary_finish','ordinary_deliver','protected','closeout','final_only'])
def test_all_final_contract_entries_share_exact_frozen_coverage_gate(tmp_path,entry):
    root=prepared(tmp_path);job,payload,_=raw_author(root,review=comparison())
    if entry=='ordinary_deliver':payload['next_action']='deliver'
    if entry=='protected':job=dict(job,final_report=dict(source_cutoff=2,stopped_original=None),search=None)
    if entry=='closeout':job=dict(job,purpose='closeout',source_cutoff=2,search=None)
    if entry=='final_only':job=dict(job,final_only=True,search=None)
    payload['final_coverage']=None
    with pytest.raises(OutputRepairRequired,match='final_coverage'):nominations.require_final_coverage(root,job,payload)
    fixture_final_coverage(root,job,payload)
    nominations.require_final_coverage(root,job,payload)
    # FINAL does not exempt the same exact completed W from B1.
    payload['portfolio_review']=None
    with pytest.raises(OutputRepairRequired,match='portfolio_review'):search.require_portfolio_review(root,job,payload)


def test_no_search_and_independent_formal_review_do_not_create_coverage(tmp_path):
    root=prepared(tmp_path,search_enabled=False);job,payload,record=raw_author(root,review=None)
    payload.pop('final_coverage',None);rewrite(record,payload);c.seal_result(root,3)
    assert 'final_coverage' not in job['scientific_response_fields']
    nominations.require_final_coverage(None,dict(role='A',formal_review=dict(subject='exact')),dict(next_action='finish'))


def protected_root(tmp_path):
    from scidiscovery.research_loop import controller
    from test_history_handoff import finish
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('Bounded final scientific summary')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['never-launched'],max_tokens=100000,max_seconds=600,max_steps=12,
        model='fixture',reasoning_effort='high',node_budget_policy={role:dict(max_observed_tokens=10000,max_seconds=120) for role in 'HWAE'})
    finish(root,1,'H',[],action='experiment',search_choice=dict(scope='Two explanations',commitments=[dict(
        label=label,minimal_test='A discriminating prediction',existing_ref=None) for label in ('Main','Competitor')],
        focus=dict(label='Main',action='improve')))
    finish(root,2,'W',[1])
    return root


def test_real_protected_final_job_seal_and_replay_close_exact_pending_cycle(tmp_path):
    root=protected_root(tmp_path)
    job=c.build_job(root,3,'A',[1,2],final_report=dict(source_cutoff=2,stopped_original=None))
    assert job['final_only'] and 'search' not in job and len(job['final_coverage_mechanisms'])==2
    _,payload,record=raw_author(root,review=comparison())
    payload['final_coverage']=None;rewrite(record,payload)
    with pytest.raises(OutputRepairRequired,match='final_coverage'):c.seal_result(root,3)
    fixture_final_coverage(root,job,payload);rewrite(record,payload);c.seal_result(root,3)
    assert c.validate(c.workspace(root,3))['final_coverage']==payload['final_coverage']
    saved=c.read(root/'host-history/overview.json')['portfolio_metrics']
    assert saved['source_step']==3 and saved['portfolio']['completed_cycle_count']==1
    assert saved['portfolio']['pending_assessment'] is False


def test_protected_final_snapshot_uses_frozen_cut_and_omits_unstarted_w(tmp_path):
    from scidiscovery.research_loop import budget
    root=protected_root(tmp_path);_,_,first=raw_author(root,review=comparison());c.seal_result(root,3)
    c.build_job(root,4,'W',[1,3])
    stopped=budget.stopped_report_original(root,4)
    job=c.build_job(root,5,'A',[1,3],final_report=dict(source_cutoff=3,stopped_original=stopped))
    _,payload,record=raw_author(root,5,(1,3),review=None)
    c.seal_result(root,5)
    assert c.validate(c.workspace(root,5))['final_coverage']==payload['final_coverage']
    saved=c.read(root/'host-history/overview.json')['portfolio_metrics']
    assert saved['source_step']==5 and saved['portfolio']['completed_cycle_count']==1
    assert saved['portfolio']['pending_assessment'] is False
    assert not (execution.origin(root,4)/'result-identity.json').exists()
