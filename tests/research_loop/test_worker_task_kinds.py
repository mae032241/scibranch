"""Scientific authors choose W work; the host never guesses from predecessor role."""
import pytest

from scidiscovery.research_loop import contracts as c, controller, search
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_history_handoff import finish


def instance(tmp_path, *, enabled=True):
    public=tmp_path/'public';public.mkdir()
    objective=public/'goal.md';objective.write_text('Distinguish mechanisms before allocating deeper work.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['no-model'],max_tokens=100000,max_seconds=600,max_steps=12,
        model='fixture',reasoning_effort='high',search_policy=search.DEFAULT_POLICY if enabled else None)
    return root


def hypothesis(root,focus):
    return finish(root,1,'H',[],action='experiment',search_choice=dict(scope='Two scientific mechanisms',
        commitments=[dict(label=name,minimal_test='A discriminating observation with retained limitations',existing_ref=None)
                     for name in ('P','Q')],focus=focus))


@pytest.mark.parametrize('action',['improve','validate'])
def test_h_can_explicitly_request_local_work_without_repeating_breadth(tmp_path,action):
    root=instance(tmp_path)
    hypothesis(root,dict(index=1,action=action))
    job=c.build_job(root,2,'W',[1])
    assert search.launch_reason(root,job) is None
    assert job['search']['focus']==dict(mechanism_ref='h001.m1',action=action)
    assert job['search']['coverage_obligation']==[]
    assert [item['mechanism_ref'] for item in job['search']['breadth']['mechanisms']]==['h001.m1']
    # Q remains scientifically pending; selecting local work is not a Q verdict.
    assert search.project(root,2)['mechanisms']['h001.m2']['status']=='pending'
    finish(root,2,'W',[1],mechanism_findings=None)
    assert c.validate(c.workspace(root,2))['role']=='W'


def test_ordinary_a_advice_cannot_return_w_to_combination_screening(tmp_path):
    root=instance(tmp_path)
    hypothesis(root,dict(index=1,action='improve'))
    finish(root,2,'W',[1],mechanism_findings=None)
    finish(root,3,'A',[1,2],action='continue_w',search_choice=dict(dispositions=[],
        next_focus=dict(mechanism_ref=None,action='screen')))
    job=c.build_job(root,4,'W',[1,2,3])
    assert search.launch_reason(root,job) is None
    assessor=c.read(root/'jobs/003.json')
    assert assessor['research_strategy_version']==3 and assessor['advisory_scope_version']==1
    assert c.validate(c.workspace(root,3))['decision_request'] is None
    assert job['search']['focus']==dict(mechanism_ref='h001.m1',action='improve')
    assert job['search']['coverage_obligation']==[]
    assert [row['mechanism_ref'] for row in job['search']['breadth']['mechanisms']]==['h001.m1']
    assert search.project(root,4)['mechanisms']['h001.m2']['status']=='pending'


@pytest.mark.parametrize('focus',[None,*[dict(index=None,action=action) for action in ('improve','repair','validate')]],
    ids=['missing','null-improve','null-repair','null-validate'])
def test_h_cannot_seal_an_experiment_without_an_unambiguous_task_choice(tmp_path,focus):
    root=instance(tmp_path)
    c.build_job(root,1,'H',[])
    original=(root/'jobs/001.json').read_bytes()
    with pytest.raises(OutputRepairRequired,match='search_plan.focus'):
        hypothesis(root,focus)
    record=c.host_record(c.workspace(root,1))
    payload=c.read(record/'science-output.json')
    assert payload['search_plan']['focus']==focus
    assert payload['scientific_result']=='A bounded scientific finding'
    assert (root/'jobs/001.json').read_bytes()==original
    assert not (record/'result-identity.json').exists()
    assert not (root/'jobs/002.json').exists()


def test_nonsearch_worker_has_no_combination_obligation(tmp_path):
    root=instance(tmp_path,enabled=False)
    finish(root,1,'W',[],mechanism_findings=None)
    assert c.validate(c.workspace(root,1))['role']=='W'
