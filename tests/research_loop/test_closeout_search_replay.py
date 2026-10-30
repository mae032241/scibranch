"""Closeout receipts replay neutrally without relaxing science integrity."""
import copy

import pytest

from scidiscovery.research_loop import (budget, candidate_audit, contracts as c,
    controller, execution, native, search, semantic)
from scidiscovery.research_loop.protocol import CURRENT
from test_history_handoff import finish


def scene(tmp_path,*,begin_closeout=True):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Retain the original mechanism comparison when exploration closes.')
    # This adapter is frozen solely to validate empty delivery-source metadata.
    # The real audit-unavailable branch below must never invoke it.
    adapter=tmp_path/'delivery_adapter.py';adapter.write_text('raise AssertionError("Unexpected external action")\n')
    import sys
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['never-call-a-model'],max_tokens=100000,max_seconds=600,max_steps=12,
        model='no-model',reasoning_effort='high',search_policy=search.DEFAULT_POLICY,node_budget_mode='soft',
        portfolio_policy=dict(review_after_cycles=3),
        node_budget_policy={role:dict(max_observed_tokens=2000,max_seconds=60) for role in 'HWAE'},
        delivery_policy=dict(closeout_a=dict(max_observed_tokens=1000,max_seconds=30),inflight_tokens=100,
            cleanup_seconds=5,submit_seconds=20,confirm_seconds=10,
            adapter=dict(adapter=str(adapter),launcher_argv=[sys.executable,str(adapter)],config={},target={},
                inputs=[],source_roots=[str(root)],readonly_inputs_version=1)))
    finish(root,1,'H',[],action='experiment',search_choice=dict(scope='One unfinished scientific mechanism',
        commitments=[dict(label='P',minimal_test='A discriminating observation remains necessary',existing_ref=None)],
        focus=dict(index=None,action='screen')))
    finish(root,2,'W',[1],mechanism_findings=[dict(mechanism='h001.m1',basis='blocked',
        finding='This fixture retains a concrete missing observation and does not claim scientific screening.')])
    baseline=search.project(root,3)
    assert baseline['focus']==dict(mechanism_ref='h001.m1',action='screen')
    assert baseline['order']['allocation'] is not None
    nodes=[dict(step=step,seal_ref=c.record_ref(execution.origin(root,step)/'result-identity.json')) for step in (1,2)]
    refs=dict(items=[],omitted_count=0,source_nodes=nodes)
    c.write(root/'host-started.json',budget.clock_stamp());budget.transition(root,'advance','Original zero-model fixture host start')
    if begin_closeout:budget.begin_closeout(root,2,3,'Original exploration is closed',delivery_sources=refs)
    return root,refs,baseline


def closeout(root,refs,step,parents,action='audit'):
    job=c.build_job(root,step,'A',parents,purpose='closeout',source_cutoff=2,delivery_sources=refs)
    record=execution.record(root,step)
    payload=dict(summary='A retained closeout decision',scientific_result='Uncertainty remains; no further exploration is authorized.',
        limitations=['No candidate is available in this fixture'],implementation_selection=None,candidate_recommendation=None,
        progress_assessment=None,search_review=None,decision_request=None,
        next_action=action,route_reason='Use only the frozen closeout inputs')
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json')))
    c.write(record/'science-output.json',payload)
    c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,usage=native.usage(root),
        raw_ref=c.file_ref(record/'science-output.json')))
    c.seal_result(root,step)
    return job,payload,c.read(record/'result-identity.json')


def tree(root):
    return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_closeout_audit_then_followup_a_replays_null_receipts_and_preserves_exploration(tmp_path):
    root,refs,baseline=scene(tmp_path)
    job,payload,seal=closeout(root,refs,3,[1,2])
    receipt=seal['control_outcome']
    assert 'portfolio_review' not in payload and 'portfolio_review' not in job['scientific_response_fields']
    assert 'portfolio_status' not in receipt
    assert receipt['resolved_focus'] is None and receipt['resolved_allocation'] is None
    assert receipt['search_status']=='applied' and receipt['diagnostics']==[]
    assert search.project(root,4)==baseline
    assert candidate_audit.execute(root,job,payload) is None
    audit=candidate_audit.receipt_ref(root,3)
    assert candidate_audit.public_receipt(root,audit)['status']=='unavailable'
    following,_,_=closeout(root,refs,4,[3],action='finish')
    assert following['candidate_audit']==audit
    assert search.project(root,5)==baseline
    before=tree(root)
    c.validate(c.workspace(root,3));c.validate(c.workspace(root,4));search.project(root,5)
    assert tree(root)==before


@pytest.mark.parametrize('unresolved',['selection_status','decision_status'])
def test_closeout_other_unresolved_status_never_clears_original_search_state(tmp_path,unresolved):
    root,refs,baseline=scene(tmp_path)
    job,payload,seal=closeout(root,refs,3,[1,2])
    seal=copy.deepcopy(seal);seal['control_outcome'][unresolved]='unresolved'
    state=copy.deepcopy(baseline)
    before=tree(root)
    search.apply_source(root,job,payload,seal,state)
    assert state==baseline and tree(root)==before


@pytest.mark.parametrize('field,value',[
    ('binding_protocol',CURRENT-1),('search_status','unresolved'),
    ('resolved_focus',dict(mechanism_ref='h001.m1',action='screen')),
    ('resolved_allocation',dict(mechanism_ref='h001.m1',planned_step=1)),
    ('diagnostics',[dict(code='science_task_action_required',subject='focus')]),
])
def test_closeout_tamper_still_fails_replay_and_validate_without_writes(tmp_path,field,value):
    root,refs,baseline=scene(tmp_path)
    job,payload,seal=closeout(root,refs,3,[1,2]);seal=copy.deepcopy(seal)
    seal['control_outcome'][field]=value
    before=tree(root)
    with pytest.raises(ValueError):search.apply_source(root,job,payload,seal,copy.deepcopy(baseline))
    assert tree(root)==before
    c.write(execution.record(root,3)/'result-identity.json',seal)
    before=tree(root)
    with pytest.raises(ValueError):c.validate(c.workspace(root,3))
    assert tree(root)==before


def test_ordinary_a_keeps_scientific_interpretation_and_shared_seal_guard(tmp_path):
    root,_,baseline=scene(tmp_path,begin_closeout=False)
    ordinary=c.build_job(root,3,'A',[1,2])
    payload=dict(next_action='continue_w',search_review=dict(dispositions=[],
        next_focus=dict(mechanism_ref='h001.m1',action='improve')),route_reason='A explicitly chooses deeper scientific work')
    interpreted=search.interpret(root,ordinary,payload,state=baseline)
    assert interpreted['focus']==dict(mechanism_ref='h001.m1',action='improve')
    assert interpreted['allocation'] is not None
    assert interpreted['state']!=baseline
    assert search.project(root,3)==baseline
