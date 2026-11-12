"""Optional A questions remain original advice across an exact W fork cut."""
import copy

import pytest

from scidiscovery.research_loop import accepted_budget, contracts as c, controller, execution, fork_runtime, fork_service, native, search
from test_history_handoff import finish
from test_portfolio_contract import MAIN, prepared


@pytest.mark.process_e2e
def test_fork_preserves_original_advice_and_excludes_updates_after_cut(tmp_path):
    root=prepared(tmp_path)
    advice=dict(discriminating_work=dict(existing_ref=None,mechanism_refs=[MAIN],
        question='Which observation distinguishes the explanations?',
        change_criterion='An exact contradiction changes the selected explanation.',next_action='extract'),
        deferral=dict(reason='The observation is missing.',trigger='It becomes available.',revisit='W judges when useful.'),
        work_updates=[dict(work_ref='A999-D001',action='execute',evidence=[])])
    _,_,record=finish(root,3,'A',[1,2],portfolio_review=advice)
    original=(record/'science-output.json').read_bytes()
    assert c.validate(c.workspace(root,3))['portfolio_review']==advice
    assert (record/'science-output.json').read_bytes()==original
    initial=search.project(root,4)
    assert initial['order']['discriminating_work']==[]
    assert accepted_budget.read(root)['search']['portfolio']['main_line'] is None
    # The selected task is W's own work, with this fallible A advice as input.
    finish(root,4,'W',[1,3],text='The observation remains inconclusive; W owns the next research choice.')
    later=copy.deepcopy(advice)
    later['work_updates']=[dict(work_ref='A003-D001',action='withdraw',evidence=[])]
    finish(root,5,'A',[1,4],portfolio_review=later)
    with controller._writer(root):pass
    consumption=native.usage(root)
    child=tmp_path/'child';limits=dict(max_tokens=10000,max_seconds=60,max_steps=8)
    preview=fork_service.preview(root,4,child,budget=limits)
    fork_service.create(root,4,child,budget=limits,expected_preview_id=preview['preview_id'],
        operation_id='retain-authored-question')
    job=fork_runtime.build_selected_job(child,4)
    inherited=search.project(child,4)
    parent=search.project(root,6)
    assert job['role']=='W' and job['step']==4
    assert inherited['focus']==parent['focus']==initial['focus']
    assert inherited['opportunity_counts']==parent['opportunity_counts']==initial['opportunity_counts']
    assert inherited['order']['discriminating_work']==parent['order']['discriminating_work']==[]
    assert accepted_budget.read(child)['search']['portfolio']['completed_cycle_count']==0
    assert accepted_budget.read(root)['search']['portfolio']['completed_cycle_count']==0
    assert fork_runtime.source_record(child,3,'science-output.json').read_bytes()==original
    assert native.usage(child)['observed_tokens']==0
    assert native.usage(root)==consumption and consumption['observed_tokens']>0
    assert c.settings(child)['max_tokens']==limits['max_tokens']
    assert not (execution.origin(child,4)/'native-intent.json').exists()
    assert search.validate_science_refs(child,job,[dict(source_step=3,pointer='/portfolio_review/discriminating_work/question')])
    with pytest.raises(ValueError):
        search.validate_science_refs(child,job,[dict(source_step=5,pointer='/portfolio_review/work_updates')])
