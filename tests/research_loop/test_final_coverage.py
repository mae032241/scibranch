"""Protected Final A retains authored detail and the exact completed evidence cut."""
from scidiscovery.research_loop import accepted_budget, budget, contracts as c, execution
from test_final_a_completion_contract import complete
from test_history_handoff import finish
from test_portfolio_contract import MAIN, prepared, rewrite


def final_author(root,step,parents,cutoff,stopped=None):
    budget.begin_closeout(root,cutoff,step,'Explicit fixture FINAL phase',final_only=True,stopped_original=stopped)
    job=c.build_job(root,step,'A',parents,final_report=dict(source_cutoff=cutoff,stopped_original=stopped))
    record=execution.origin(root,step)
    intent=dict(step=step,task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'),
        budget_slice=budget.accept_node(root,job))
    c.write(record/'native-intent.json',intent)
    accepted_budget.accept_native(root,step,record,intent)
    complete(root,job,intent)
    return job,c.read(record/'science-output.json'),record


def test_real_protected_final_job_seal_and_replay_close_exact_pending_cycle(tmp_path):
    root=prepared(tmp_path)
    job,payload,record=final_author(root,3,[1,2],2)
    assert job['final_only'] and 'search' not in job
    # This optional account may be partial; the citation remains authored advice.
    coverage=[dict(mechanism_ref=MAIN,status='deep',
        reason='The retained proxy does not establish target-condition adequacy.',
        unanswered_questions=['Does the proxy transfer to the target condition?'],
        science_refs=[dict(source_step=99,pointer='/scientific_result')])]
    payload['final_coverage']=coverage
    payload.pop('portfolio_review')
    rewrite(record,payload);original=(record/'science-output.json').read_bytes()
    c.seal_result(root,3)
    assert c.validate(c.workspace(root,3))['final_coverage']==coverage
    assert c.seal_result(root,3)['final_coverage']==coverage
    assert (record/'science-output.json').read_bytes()==original
    saved=accepted_budget.read(root)['search']['portfolio']
    assert saved['last_decision_step']==3 and saved['completed_cycle_count']==1
    assert saved['pending_w'] is None


def test_protected_final_snapshot_uses_frozen_cut_and_omits_unstarted_w(tmp_path):
    root=prepared(tmp_path)
    _,_,first=finish(root,3,'A',[1,2])
    c.build_job(root,4,'W',[1,3])
    stopped=budget.stopped_report_original(root,4)
    boundary=dict(source_cutoff=3,stopped_original=stopped)
    originals=[root/'jobs/003.json',root/'jobs/004.json',first/'science-output.json',first/'result-identity.json']
    before={path:path.read_bytes() for path in originals}
    job,payload,record=final_author(root,5,[1,3],3,stopped)
    payload.pop('final_coverage',None);payload.pop('portfolio_review')
    rewrite(record,payload);original=(record/'science-output.json').read_bytes()
    c.seal_result(root,5)
    assert 'final_coverage' not in c.validate(c.workspace(root,5))
    assert job['final_report']==boundary and job['final_only'] and 'search' not in job
    assert job['history']['prefix']['cutoff']==3
    saved=accepted_budget.read(root)['search']['portfolio']
    assert saved['last_decision_step']==5 and saved['completed_cycle_count']==1
    assert saved['pending_w'] is None
    assert (record/'science-output.json').read_bytes()==original
    assert all(path.read_bytes()==data for path,data in before.items())
    assert not (execution.origin(root,4)/'native-intent.json').exists()
    assert not (execution.origin(root,4)/'result-identity.json').exists()
