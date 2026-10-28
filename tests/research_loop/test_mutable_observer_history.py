"""Mutable observations and retained scientific originals have distinct custody."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c, history
from test_history import setup_observer
from test_history_handoff import finish, scene
from execution_fixtures import execution_registry


def test_small_live_original_survives_same_size_atomic_replacement(scene):
    path=scene/'live.json';path.write_bytes(b'first')
    proof=history._proof(scene,'live.json')
    pending=scene/'next.json';pending.write_bytes(b'other');pending.replace(path)
    history._validate_proof(scene,proof)
    assert proof['snapshot']!='live.json'
    assert (scene/proof['snapshot']).read_bytes()==b'first'
    retained=scene/proof['snapshot']
    retained.chmod(0o600);retained.write_bytes(b'other')
    with pytest.raises(history.ObserverIntegrityError,match='Archived observer original changed'):
        history._validate_proof(scene,proof)


@pytest.mark.parametrize('readonly',[False,True])
def test_live_artifact_never_acquires_managed_custody(scene,tmp_path,readonly):
    setup_observer(scene,tmp_path,'''import json
print(json.dumps(dict(candidates=[],current_candidate=None,
 actual_submission=dict(candidate_id=None,artifact='model.bin',evidence=['fact.json']),
 coverage=dict(status='complete',note='Public observation only'))))
''')
    path=scene/'model.bin';path.write_bytes(b'payload')
    if readonly:path.chmod(0o400)
    (scene/'fact.json').write_bytes(b'{}')
    finish(scene,1,'H',[])
    fact=c.read(scene/'host-history/observations/001.json')
    assert fact['actual_submission']['artifact']==dict(path='model.bin',size_bytes=7,retention='unavailable')
    assert fact['coverage']['status']=='partial'
    path.chmod(0o600);path.write_bytes(b'changed')
    c.validate(c.workspace(scene,1))


def test_capture_reuses_managed_output_control_binding_across_promotions(tmp_path,monkeypatch):
    import sys
    from scidiscovery.research_loop import controller,execution,experiments,shared_originals
    from cov_execution import delivery as provider,observer
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Check retained output custody without executing science.')
    scene=tmp_path/'managed-instance'
    controller.prepare(scene,objective,public,['fake-no-model'],max_tokens=1000,max_seconds=60,max_steps=6,
        model='fixture',reasoning_effort='high',search_policy=None,experiment_mode='local',
        execution_config=dict(executor='local_execution:command',
            configuration=dict(run_argv=[sys.executable],max_seconds=30)))
    setup_observer(scene,tmp_path,'# Transport is injected below; no scientific adapter submission.')
    finish(scene,1,'H',[],action='experiment')
    job=c.build_job(scene,2,'W',[1])
    c.write(execution.raw(scene,2),dict(summary='Unsealed component observation'))
    original=shared_originals.publish_bytes(scene,b'opaque scientific output; never executed')
    saved=experiments.proof(original)
    output=dict(name='model.bin',path=original.relative_to(scene).as_posix(),
        snapshot=original.relative_to(scene).as_posix(),retention='archived',
        **{key:saved[key] for key in ('size_bytes','file_stat')})
    # This component fixture supplies an already retained host output inventory.
    # It replaces discovery only: no executor, qualification or scientific
    # verdict is supplied, and no scientific execution is requested.
    assert history._managed_config(job,scene) is not None
    monkeypatch.setattr(history,'managed_calls',lambda root,current:[dict(
        call_id='retained-component',scope=dict(step=current['step']),outputs=[output])])
    monkeypatch.setattr(history,'read_adapter',lambda config,request:observer.observe(dict(request,
        config=dict(runs='runs',evaluation_key='component-only'))))
    runs=scene/'runs';runs.mkdir();submission=scene/'submission';submission.mkdir()
    retain=shared_originals.retain;copied=[]
    def retain_metadata(root,path,**options):
        assert Path(path)==runs/'submitted.json'
        copied.append(Path(path))
        return retain(root,path,**options)
    monkeypatch.setattr(shared_originals,'retain',retain_metadata)
    request=dict(operation_id='fixture-promotion',candidate_id='run-1',
        artifact_ref={key:saved[key] for key in ('path','size_bytes')},
        artifact_origin={key:saved[key] for key in ('path','size_bytes')},target=dict(runs=str(runs)))
    provider._promote(request,runs,submission,original,dict(run_id=1))
    receipt=history.capture(scene,job,{},[])
    fact=c.read(scene/receipt['path']);actual=fact['actual_submission']
    artifact=actual['artifact'];metadata=actual['evidence'][0]
    assert artifact['retention']=='archived'
    assert Path(artifact['snapshot'])==original
    assert artifact['file_stat'][1]==original.stat().st_ino
    assert metadata['snapshot']!='runs/submitted.json'
    original_metadata=(scene/metadata['snapshot']).read_bytes()
    provider._promote(dict(request,operation_id='fixture-next'),runs,submission,original,dict(run_id=1))
    for proof in (artifact,metadata):history._validate_proof(scene,proof)
    assert (scene/metadata['snapshot']).read_bytes()==original_metadata
    assert history.capture(scene,job,{},[])==receipt
    assert c.read(scene/receipt['path'])==fact
    assert copied==[runs/'submitted.json']
    original.chmod(0o600);original.write_bytes(b'tampered managed original')
    with pytest.raises(history.ObserverIntegrityError,match='Archived observer original changed'):
        history._validate_proof(scene,artifact)


def test_cov_submission_source_requires_exact_published_generation(scene):
    import importlib.util
    path=Path(__file__).resolve().parents[2]/'plugins/cov_execution/cov_execution/observer.py'
    spec=importlib.util.spec_from_file_location('cov_observer_custody',path)
    observer=importlib.util.module_from_spec(spec);spec.loader.exec_module(observer)
    runs=scene/'runs';runs.mkdir()
    model=runs/'submitted_model.pkl';model.write_bytes(b'model')
    origin=scene/'original.pkl';origin.write_bytes(b'model')
    fact=dict(run_id=1)
    info=model.stat()
    marker=dict(operation_id='fixture-op',candidate_id='run-1',audit=fact,artifact_origin=dict(path=str(origin),size_bytes=5),
        published=dict(run_model=[info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns]))
    c.write(runs/'rootless-delivery-operation.json',marker)
    assert observer.submission_original(scene,runs,model,fact)=='original.pkl'
    replacement=runs/'replacement.pkl';replacement.write_bytes(b'model');replacement.replace(model)
    assert observer.submission_original(scene,runs,model,fact)=='runs/submitted_model.pkl'
