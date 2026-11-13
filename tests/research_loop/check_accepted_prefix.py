"""Accepted-prefix handoff boundaries, without a native or executor process."""
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import accepted_budget,accepted_prefix,contracts as c,controller,native
from scidiscovery.research_loop import worker_execution_bridge,experiments
from scidiscovery.research_loop import fork_service,fork_runtime,originals


@pytest.fixture
def instance(tmp_path):
    public=tmp_path/'public';public.mkdir()
    objective=public/'goal.md';objective.write_text('Retain exact original synthetic findings.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['fixture-never-launch'],max_tokens=100000,
        max_seconds=600,max_steps=40,model='fixture',reasoning_effort='high',search_policy=None)
    return root


def seal(root,step,*,parents=(),selection=None):
    job=c.build_job(root,step,'E',list(parents))
    record=root/'native-evidence'/f'{step:03d}'
    intent=dict(step=step,task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'))
    c.write(record/'native-intent.json',intent)
    accepted_budget.accept_native(root,step,record,intent)
    work=c.workspace(root,step)
    (work/'artifacts/subject.txt').write_text(f'Original {step}.\n')
    c.write(record/'science-output.json',dict(summary='A retained contribution',
        scientific_result='Synthetic finding; no scientific effectiveness asserted.',
        limitations=['No model or solver was run.'],implementation_selection=selection))
    c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,native_started=False,
        usage=native.usage(root),raw_ref=c.file_ref(record/'science-output.json')))
    c.seal_result(root,step)
    return job,record


def test_prefix_is_thin_and_public_navigation_reuses_one_accepted_science_body(instance):
    for step in range(1,5):seal(instance,step,parents=[step-1] if step>1 else [])
    job=c.build_job(instance,5,'E',[4])
    assert 'nodes' not in job['history'] and 'files' not in job['history']
    prefix=accepted_prefix.for_job(job)
    assert prefix['count']==4 and prefix['cutoff']==4
    node=accepted_prefix.navigation(instance,prefix)
    assert [row['step'] for row in node['recent']]==[3,4]
    public=accepted_prefix.visibility(instance,prefix)
    science=public/prefix['tip']['navigation']['path']
    assert (science.parent/'science.json').samefile(instance/'native-evidence/004/result.json')
    assert str(instance) not in json.dumps(node)


def test_exact_old_implementation_remains_selectable_without_full_history_arrays(instance):
    for step in range(1,9):seal(instance,step,parents=[step-1] if step>1 else [])
    original=instance/'project/nodes/001/artifacts/subject.txt'
    identity=original.stat()
    seal(instance,9,parents=[8],selection=['E001-I001'])
    job=c.build_job(instance,10,'E',[9])
    chosen=job['selected_inputs'][0]
    assert chosen['label']=='E001-I001' and chosen['origin_step']==1
    assert chosen['material']['source']==str(original)
    current=original.stat()
    assert (current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns)==(
        identity.st_dev,identity.st_ino,identity.st_size,identity.st_mtime_ns)
    assert 'nodes' not in job['history'] and 'files' not in job['history']


def test_old_prefix_cannot_accept_a_later_member_by_merely_raising_its_cutoff(instance):
    seal(instance,1)
    old=accepted_budget.read(instance)['prefix']
    seal(instance,2,parents=[1])
    with pytest.raises(ValueError,match='outside its accepted branch prefix'):
        accepted_prefix.member(instance,dict(old,cutoff=2),2)
    other=dict(old,visibility_root=dict(path='host-history/accepted-public/other-branch'))
    accepted_prefix.visibility(instance,old).parent.joinpath('other-branch').mkdir()
    with pytest.raises(FileNotFoundError):accepted_prefix.member(instance,other,1)


@pytest.mark.parametrize('choice',['h001.m1','0','01','-1'])
def test_public_execution_bridge_refuses_noncanonical_mechanism_choices(tmp_path,choice):
    with pytest.raises(experiments.CallerError,match='positive decimal'):
        worker_execution_bridge._arguments(['run','--mechanism',choice],tmp_path)


def test_public_execution_bridge_preserves_the_actual_catalog_number(tmp_path):
    assert worker_execution_bridge._arguments(['run','--mechanism','17'],tmp_path)==['run','--mechanism','17']


def test_fork_uses_selected_task_input_snapshot_and_freezes_public_members(instance,tmp_path):
    for step in range(1,5):seal(instance,step,parents=[step-1] if step>1 else [])
    selected=c.read(instance/'jobs/003.json')
    input_ref=selected['history']['breadth_snapshot']
    before=c.read(originals.resolve_root_ref(instance,input_ref))
    assert before['node_count']==2
    with controller._writer(instance):pass
    child=tmp_path/'fork'
    limits=dict(max_tokens=10000,max_seconds=60,max_steps=8)
    preview=fork_service.preview(instance,3,child,budget=limits)
    assert preview['status']=='fork_preview_ready',preview
    created=fork_service.create(instance,3,child,budget=limits,
        expected_preview_id=preview['preview_id'],operation_id='snapshot-input-cut')
    assert created['state']=='paused',created
    head=accepted_budget.read(child)
    assert head['search']['node_count']==2
    assert head['search']['table']==before['table']
    assert head['prefix']['count']==2
    assert head['prefix']['branch']['goal_id']!=selected['history']['prefix']['branch']['goal_id']
    with pytest.raises(FileNotFoundError):accepted_prefix.member(child,head['prefix'],3)
    public=accepted_prefix.visibility(child,head['prefix'])
    nav=accepted_prefix.navigation(child,head['prefix'],2)
    member=accepted_prefix.member(child,head['prefix'],2)
    assert (public/member['navigation']['path']).parent.joinpath('scientific-original.json').samefile(
        instance/'native-evidence/002/science-output.json')
    assert nav['step']==2
    rebuilt=fork_runtime.build_selected_job(child,3)
    assert rebuilt['history']['prefix']==head['prefix']
    assert rebuilt['history']['breadth_snapshot']==head['search_ref']


def test_cold_controller_without_disposable_route_starts_at_the_current_accepted_job(instance,monkeypatch):
    from scidiscovery.research_loop import handoff_state
    # No direct predecessor input: an old seal is irrelevant to this routing check.
    for step in range(1,9):seal(instance,step)
    (instance/'host-history/controller-route.json').unlink(missing_ok=True)
    read=c.read
    class RoutedCurrent(Exception):pass
    def observed(path):
        path=Path(path)
        if path.parent==instance/'jobs':
            assert int(path.stem)==8,'Cold controller reopened an old scientific job'
        if path.name in {'result-identity.json','result.json','science-output.json'} and path.is_relative_to(instance/'native-evidence'):
            assert path.parent.name=='008','Cold controller reopened old scientific facts'
        return read(path)
    def routed(root,step,role,result,job,current_h,current_w):
        assert step==8 and role=='E' and job['step']==8
        raise RoutedCurrent
    monkeypatch.setattr(c,'read',observed)
    monkeypatch.setattr(controller,'_route',routed)
    assert handoff_state.current(instance) is None
    with controller._writer(instance),pytest.raises(RoutedCurrent):
        controller._run(instance)
