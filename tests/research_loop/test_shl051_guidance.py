"""Current guidance reaches prepared tasks without rewriting frozen originals."""
import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, guidance, handoff_state
from test_history_handoff import finish


def instance(tmp_path, *, custom_worker=False):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md'
    objective.write_text('Resolve the original objective within its hard input constraints.')
    root=tmp_path/'instance'
    options={}
    if custom_worker:
        options['role_guidance']=dict(guidance.DEFAULT,W='Custom frozen W instruction: inspect retained evidence.')
    controller.prepare(root,objective,public,['fixture-never-launch'],max_tokens=100000,
        max_seconds=120,max_steps=8,model='deterministic-no-model',reasoning_effort='high',
        search_policy=None,supervision_policy=None,**options)
    c.write(root/'host-started.json',budget.clock_stamp())
    return root


@pytest.mark.parametrize('custom_worker',[False,True])
def test_worker_handoff_routes_reassessment_into_task_and_preserves_frozen_job(tmp_path,monkeypatch,custom_worker):
    root=instance(tmp_path,custom_worker=custom_worker)
    c.build_job(root,1,'W',[],instruction='Exact scientific question')
    worker,_,record=finish(root,1,'W',[],stage_intent='handoff',files={'observation.txt':'Retained scoped evidence.'})
    source_originals={path:path.read_bytes() for path in (root/'jobs/001.json',
        record/'science-output.json',record/'result-identity.json')}
    route=controller._route(root,1,'W',handoff_state.read_sealed(root,1),worker,None,1)
    assert route.role=='W' and route.parents==(1,)
    successor=c.build_job(root,2,route.role,list(route.parents),instruction=route.instruction)
    task_path=c.workspace(root,2)/'science-task.json'
    question=c.read(task_path)['question']
    assert question.index('original objective and hard constraints') < question.index('results and their applicability')
    assert question.index('results and their applicability') < question.index('continue, revise or stop that direction')
    assert 'inputs/parents/001/conclusion.json' in question
    for retained in ('original evidence','unfinished questions','candidates','materials','mechanism history',
                     'consumed budgets/opportunities','permissions and global budgets persist'):
        assert retained in question
    assert 'Preserve original directions' not in question
    assert any(row['path']=='inputs/parents/001/artifacts/observation.txt' for row in successor['direct_materials'])
    if custom_worker:
        assert successor['native_role_instruction']=='Custom frozen W instruction: inspect retained evidence.'
    assert all(path.read_bytes()==data for path,data in source_originals.items())
    originals=dict(source_originals)
    originals.update({path:path.read_bytes() for path in (root/'settings.json',root/'jobs/002.json',task_path)})
    monkeypatch.setattr(guidance,'worker_handoff',lambda *args:'Changed future handoff advice.')
    assert c.build_job(root,2,'W',[1],instruction=route.instruction)==successor
    assert all(path.read_bytes()==data for path,data in originals.items())


@pytest.mark.parametrize('has_worker',[False,True])
def test_final_producer_freezes_guidance_once_and_retains_missing_worker_qualification(tmp_path,monkeypatch,has_worker):
    root=instance(tmp_path)
    finish(root,1,'W' if has_worker else 'H',[],stage_intent='handoff')
    begin=budget.begin_closeout(root,1,2,'Bounded final guidance fixture',final_only=True)
    final_instruction=guidance.final_report
    calls=[]
    def current_final():
        calls.append('guidance')
        return final_instruction()
    def stop_before_native(root,step,**kwargs):
        assert step==2
        raise RuntimeError('Deterministic stop before native execution')
    monkeypatch.setattr(guidance,'final_report',current_final)
    monkeypatch.setattr(controller,'_native_call',stop_before_native)
    assert controller._run_final(root,begin)['status']=='closeout_native_unsealed'
    assert calls==['guidance']
    job_path=root/'jobs/002.json';task_path=c.workspace(root,2)/'science-task.json'
    job=c.read(job_path);question=c.read(task_path)['question']
    assert job['final_only'] and job['role']=='A'
    assert 'Candidate availability never requires scientific acceptance' in question
    assert 'Use finish to decline every candidate' in question
    assert 'Use deliver only to explicitly select a justified exact existing candidate' in question
    assert 'No new role, experiment, audit, wait or correction is authorized' in question
    assert ('No accepted W evidence exists' in question) is (not has_worker)
    originals={path:path.read_bytes() for path in (root/'settings.json',job_path,task_path)}
    def forbidden_regeneration():
        raise AssertionError('An existing FINAL task must retain its frozen instruction')
    monkeypatch.setattr(guidance,'final_report',forbidden_regeneration)
    assert controller._run_final(root,begin)['status']=='closeout_native_unsealed'
    assert all(path.read_bytes()==data for path,data in originals.items())


def test_accepted_final_finish_keeps_scientific_decline_reason(tmp_path,monkeypatch):
    root=instance(tmp_path)
    finish(root,1,'W',[],stage_intent='handoff')
    begin=budget.begin_closeout(root,1,2,'Bounded final decline fixture',final_only=True)
    job=c.build_job(root,2,'A',[1],instruction=guidance.final_report(),
        final_report=dict(source_cutoff=1,stopped_original=None))
    record=c.host_record(c.workspace(root,2))
    intent=dict(step=2,task_ref=c.record_ref(root/'jobs/002.json'),budget_slice=budget.accept_node(root,job))
    c.write(record/'native-intent.json',intent)
    accepted_budget.accept_native(root,2,record,intent)
    monkeypatch.setattr(budget,'final_a_return',lambda *args:dict(step=2,basis='final_state'))
    from scidiscovery.research_loop import delivery
    def forbidden_delivery(*args,**kwargs):
        raise AssertionError('A final scientific decline must not prepare delivery')
    monkeypatch.setattr(delivery,'prepare_binding',forbidden_delivery)
    result=controller._final_a_result(root,job,dict(next_action='finish',candidate_recommendation=None))
    assert result['status']=='final_a_completed'
    assert result['reason']=='scientific_delivery_declined'
    assert result['submission_result']['status']=='unsubmitted'
    assert budget.closeout_state(root)['begin']==begin
    assert budget.closeout_state(root)['actions']==[]
    assert not (root/'jobs/003.json').exists()
