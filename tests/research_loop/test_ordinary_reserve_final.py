"""Actual shared usage admits ordinary work; one explicit FINAL owns completion."""
import sys

import pytest

from scidiscovery.research_loop import (accepted_budget, budget, contracts as c, controller,
    execution, experiment_client, experiments, feedback, native, search)
from execution_fixtures import execution_registry
from test_final_a_completion import LAUNCHER, node_hint_facts
from test_history_handoff import finish


def instance(tmp_path, *, remaining=211692, mode='soft', action='experiment'):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Compare an authorized adaptation against retained controls.')
    launcher=tmp_path/'native.py';launcher.write_text(LAUNCHER)
    c.write(launcher.with_suffix('.json'),dict(worker_tokens=0))
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable,str(launcher)],
        max_tokens=remaining+10,max_seconds=1200,max_steps=8,
        model='deterministic-fixture-no-model',reasoning_effort='high',node_budget_mode=mode,
        search_policy=search.DEFAULT_POLICY,
        node_budget_policy={role:dict(max_observed_tokens=300000 if role=='A' else 900000,max_seconds=60)
            for role in 'HWAE'},
        delivery_policy=dict(closeout_a=dict(max_observed_tokens=300000,max_seconds=60),
            inflight_tokens=50000,cleanup_seconds=30,submit_seconds=0,confirm_seconds=0,adapter=None))
    plan=dict(scope='Compare adaptation with the retained controls',
        commitments=[dict(label='Adaptation',minimal_test='Compare the paired recovery measurement',existing_ref=None)],
        focus=dict(index=1,action='improve')) if action=='experiment' else None
    finish(root,1,'H',[],action=action,search_choice=plan)
    return root


@pytest.mark.process_e2e
def test_ordinary_improve_below_old_reserve_can_accept_first_managed_call(tmp_path,monkeypatch,execution_registry):
    remaining=211692
    root=instance(tmp_path,remaining=remaining)
    settings=(root/'settings.json').read_bytes()
    assert budget.delivery_reserve(c.settings(root))==dict(tokens=0,seconds=0.,steps=0)
    accepted=[]
    def execute(root,step):
        assert step==2
        job=c.read(root/'jobs/002.json')
        assert job['role']=='W' and job['search']['focus']['action']=='improve'
        assert not job.get('final_only') and budget.node_admission(root,job) is None
        anchor=budget.accept_node(root,job)
        record=execution.origin(root,step)
        intent=dict(step=step,task_ref=c.record_ref(root/'jobs/002.json'),budget_slice=anchor,
            search_slice=search.accept(root,job))
        c.write(record/'native-intent.json',intent)
        accepted_budget.accept_native(root,step,record,intent)
        limits,facts=node_hint_facts(root,job,intent,dict(input_tokens=0,cached_input_tokens=0,output_tokens=0),observed=False)
        budget.write_node_hint(root,job,intent,limits,dict(input_tokens=0,cached_input_tokens=0,output_tokens=0),facts=facts,observed=False)
        hint=feedback.budget_hint(c.workspace(root,step))
        assert hint['reserve_state']=='within'
        assert hint['current_work_observed_tokens_remaining_upper_bound']==remaining
        config_path=tmp_path/'execution-config.json'
        experiments.configure(root,step,'local_execution:command',dict(run_argv=[sys.executable]),config_path)
        config=c.read(config_path)
        assert not experiments.review_status(config)['requested']
        script=c.workspace(root,step)/'work/adaptation.py';script.write_text('print("mechanical admission fixture only")\n')
        result=experiments.dispatch(config,script,new=True,mechanism='Adaptation')
        assert result['status']=='known_success',result
        accepted.extend(experiments.Records(config).calls())
        payload=dict(summary='Returned the authorized observation',scientific_result='Mechanical admission only; no scientific qualification.',
            limitations=['Deterministic fixture'],implementation_selection=None,mechanism_findings=None,stage_intent='decision',
            candidate_nominations=dict(representatives=[],extras=[],gaps=[dict(
                mechanism_ref=row['mechanism_ref'],reason='The mechanical command produces no scientifically suitable model candidate.')
                for row in job['candidate_nomination_mechanisms'] if row['status']!='waived']))
        c.write(record/'science-output.json',payload)
        c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
        c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,
            usage=native.usage(root),raw_ref=c.file_ref(record/'science-output.json')))
        c.seal_result(root,step)
        (root/'pause.request').write_text('Pause after the accepted original')
    monkeypatch.setattr(native,'execute_native',execute)
    result=controller.run(root)
    assert result['status']=='paused' and result['completed_steps']==2,result
    assert len(accepted)==1 and budget.final_a_barrier(root) is None
    assert c.read(accepted[0]/'accepted.json')['mechanism_binding']['mechanism_ref']=='h001.m1'
    assert (execution.origin(root,2)/'result-identity.json').is_file()
    assert native.usage(root)['observed_tokens']==20
    assert (root/'settings.json').read_bytes()==settings


@pytest.mark.process_e2e
@pytest.mark.parametrize('remaining',[0,-1])
def test_actual_shared_exhaustion_skips_ordinary_improve_and_runs_one_final(tmp_path,remaining):
    root=instance(tmp_path,remaining=remaining)
    original=(execution.origin(root,1)/'usage.json').read_bytes()
    result=controller.run(root)
    assert result['status']=='final_a_completed',result
    job=c.read(root/'jobs/002.json')
    assert job['role']=='A' and job['final_only']
    grant=budget.final_a_barrier(root)
    assert grant['basis']=='final_state' and grant['step']==2
    assert c.read(execution.origin(root,2)/'native-intent.json')['budget_slice']['final_return']
    assert not (root/'jobs/003.json').exists()
    assert not list(experiment_client.record_directory(root,2).glob('calls/*/accepted.json'))
    assert controller.run(root)['status']=='final_a_completed'
    assert (execution.origin(root,1)/'usage.json').read_bytes()==original
    assert native.usage(root)['observed_tokens']==280944


@pytest.mark.parametrize('role','HWAE')
def test_positive_shared_balance_does_not_require_future_role_tokens(tmp_path,role):
    root=instance(tmp_path,remaining=1)
    assert budget.node_admission(root,dict(step=2,role=role)) is None


def test_analysis_with_shared_balance_needs_no_managed_execution(tmp_path):
    root=instance(tmp_path,remaining=1,action='analyze')
    job=c.build_job(root,2,'A',[1])
    assert job['role']=='A' and not job.get('final_only')
    assert budget.node_admission(root,job) is None
    assert not experiment_client.record_directory(root,2).exists()


def test_budget_projection_uses_existing_usage_without_current_execution_results(tmp_path,monkeypatch):
    root=instance(tmp_path,remaining=211692)
    job=c.build_job(root,2,'W',[1])
    limits=budget.snapshot(root);usage=native.usage(root)
    def forbidden(*args,**kwargs):
        pytest.fail('Available budget must not traverse execution results')
    monkeypatch.setattr(execution,'record',forbidden)
    assert budget.execution_available(root,job,limits,usage)['tokens']==211692


def test_protected_final_has_no_shared_time_or_step_floor(tmp_path):
    root=instance(tmp_path)
    settings=c.settings(root)
    assert budget.delivery_reserve(settings)==budget.closeout_floor(settings)==dict(tokens=0,seconds=0.,steps=0)
