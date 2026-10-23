"""Job identity comparison is public; host effects retain full private guards."""
from copy import deepcopy

import pytest

from execution_fixtures import execution_registry

from scidiscovery.research_loop import contracts as c,controller,protocol
from scidiscovery.research_loop import experiments as e
from test_local_input_sources import local_scene,authored


@pytest.fixture
def scope_scene(tmp_path):
    public=tmp_path/'public';public.mkdir();goal=public/'objective.md';goal.write_text('Bounded scope identity fixture.')
    root=tmp_path/'instance'
    controller.prepare(root,goal,public,['fixture-no-launch'],max_tokens=1000,max_seconds=60,max_steps=5,
        model='fixture',reasoning_effort='high')
    return root,c.build_job(root,1,'H',[])


def test_stage_identity_reads_exact_job_without_settings_or_attempt_policy(scope_scene,tmp_path):
    root,job=scope_scene;expected=protocol.stage_scope(root,job)
    assert expected['execution_key']==expected['job_ref']==c.record_ref(root/'jobs/001.json')
    (root/'settings.json').rename(tmp_path/'private-settings.json')
    # Origin identity remains stable even if an attempt directory exists. This
    # grants no continuation permission and does not read its hidden policy.
    (root/'native-evidence/001/continuations').mkdir()
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert protocol.stage_scope(root,job)==expected
    assert before=={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='original regular file'):
        protocol.require_current(root,job)


@pytest.mark.parametrize('damage',['different_job','old_protocol','bool_step','bad_role','symlink'])
def test_stage_identity_rejects_nonoriginal_or_noncurrent_job(scope_scene,tmp_path,damage):
    root,original=scope_scene;job=deepcopy(original);path=root/'jobs/001.json'
    if damage=='different_job':job['instruction']='Another scientific question'
    elif damage=='old_protocol':job['handoff_protocol']=protocol.CURRENT-1
    elif damage=='bool_step':job['step']=True
    elif damage=='bad_role':job['role']='operator'
    else:
        saved=tmp_path/'job.json';path.rename(saved);path.symlink_to(saved)
    with pytest.raises(ValueError):protocol.stage_scope(root,job)


@pytest.mark.parametrize('damage',[None,'projection','native_intent','continued_policy_unavailable'])
def test_initial_local_source_view_uses_bound_public_scope_and_fails_closed(local_scene,tmp_path,damage):
    root,job,config,path=local_scene;directory,script=authored(root)
    adapter=e.load(config,path=path)
    payload=e.LocalFileDescriptor(name='namespace-source-view',local_path=str(script),
        size_bytes=script.stat().st_size,media_type='text/x-python')
    metadata=adapter.acceptance_metadata(payload,command_options=dict(experiment_directory=str(directory)))
    (root/'settings.json').rename(tmp_path/'private-settings.json')
    if damage=='projection':
        projection=root/'host-runtime/experiment-control'/f'{config["scope"]["step"]:03d}'/'interfaces/000.json'
        value=c.read(projection);value['scope']['job_ref']=dict(path='/wrong/original.json');c.write(projection,value)
    elif damage=='native_intent':
        c.write(root/'native-evidence/002/native-intent.json',dict(task_ref=dict(path='/wrong/job.json')))
    elif damage=='continued_policy_unavailable':
        (root/'native-evidence/002/continuations').mkdir()
    if damage is None:
        view=adapter.input_sources(payload,metadata)
        assert view['files']==[dict(name='run.py',origin=dict(kind='author',step=2))]
        assert dict(path=str(root/view['job']['path']))==config['scope']['job_ref']
        assert view['job']['size_bytes']==(root/view['job']['path']).stat().st_size
    else:
        with pytest.raises(ValueError):adapter.input_sources(payload,metadata)
    assert not list((c.workspace(root,2)/'work/lineage/calls').glob('*/accepted.json'))
    with pytest.raises(ValueError,match='original regular file'):protocol.require_current(root,job)
