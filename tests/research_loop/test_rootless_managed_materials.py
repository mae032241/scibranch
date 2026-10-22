"""Frozen common originals cross the actual cov launcher's file-only boundary."""
import copy
import importlib.util
from pathlib import Path

import pytest

from execution_fixtures import execution_registry, remote_registry
from test_nonlocal_execution_observation import case, run
from test_history_handoff import finish
from scidiscovery.research_loop import contracts as c, execution


def prepared(tmp_path, monkeypatch):
    spec=importlib.util.spec_from_file_location('managed_cov_launcher',Path(__file__).resolve().parents[2]/'scripts/acceptance/rootless_cov_launcher.py')
    launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
    root,job,config,cli=case(tmp_path,monkeypatch)
    # Identical bytes under distinct call/descriptor identities must stay distinct.
    for label in ('P','Q'):run(root,cli,label)
    finish(root,2,'W',[1],mechanism_findings=[dict(mechanism=label,basis='observed',finding='Bounded synthetic evidence.') for label in ('P','Q')])
    job=c.build_job(root,3,'A',[1,2])
    return launcher,root,job


@pytest.mark.process_e2e
def test_frozen_managed_outputs_mount_with_exact_descriptor_and_no_backend_reload(tmp_path,monkeypatch,remote_registry):
    from scidiscovery.research_loop import experiments
    launcher,root,job=prepared(tmp_path,monkeypatch)
    monkeypatch.setattr(experiments,'load',lambda *a,**kw:pytest.fail('launcher must not query backend'))
    seal=c.read(execution.record(root,2)/'result-identity.json')
    observed=c.read(root/seal['observations']['path'])
    calls=observed['managed_calls']
    assert len(calls)==2 and len({call['call_id'] for call in calls})==2
    expected=[dict(source=str(root/output['snapshot']),
        path=f'inputs/parents/002/artifacts/experiments/{call["call_id"]}/artifacts/{output["name"]}',
        sha256=output['sha256'],size_bytes=output['size_bytes'])
        for call in calls for output in call['outputs']]
    assert len(expected)==2 and sorted(job['direct_materials'],key=lambda row:row['path'])==sorted(expected,key=lambda row:row['path'])
    assert all(row in seal['materials'] for row in expected)
    work=c.workspace(root,3)
    before={path:path.read_bytes() for path in root.rglob('*') if path.is_file()}
    # The launcher validates the existing scientific copies, so no private
    # archive needs to be mounted into the hash-free native namespace.
    assert job['worker_view_version']==1
    assert launcher.direct_materials(dict(scene_root=str(root)),work,root/'jobs/003.json')==[]
    assert len({row['path'] for row in expected})==2
    for row in expected:
        source=Path(row['source']);target=work/row['path']
        assert source==root/'host-history/objects'/row['sha256']
        assert target.is_file() and not target.is_symlink() and target.resolve()==target
        assert target.read_bytes()==source.read_bytes()==b'opaque remote package; never interpreted as local Python'
        assert target.stat().st_size==row['size_bytes'] and c.digest(target)==row['sha256']
        assert 'host-history' not in row['path'] and row['sha256'] not in row['path']
    assert before=={path:path.read_bytes() for path in root.rglob('*') if path.is_file()}


@pytest.mark.process_e2e
def test_identical_material_reference_is_an_idempotent_read_without_new_mounts(tmp_path,monkeypatch,remote_registry):
    import json
    from scidiscovery.research_loop import experiments
    launcher,root,job=prepared(tmp_path,monkeypatch)
    original_job=copy.deepcopy(job)
    repeated=copy.deepcopy(job)
    repeated['direct_materials'].append(dict(repeated['direct_materials'][0]))
    assert repeated['direct_materials'][-1]==job['direct_materials'][0]
    manifest=root/'jobs/003.json'
    original_read=launcher.regular
    # Exercise only this read helper; the accepted manifest on disk stays exact.
    def read_repeated_reference(path,bound=None):
        return json.dumps(repeated).encode() if Path(path)==manifest else original_read(path,bound)
    monkeypatch.setattr(launcher,'regular',read_repeated_reference)
    monkeypatch.setattr(experiments,'load',lambda *a,**kw:pytest.fail('identical reference must not query backend'))
    before={path:path.read_bytes() for path in root.rglob('*') if path.is_file()}
    for _ in range(2):
        assert launcher.direct_materials(dict(scene_root=str(root)),c.workspace(root,3),manifest)==[]
        assert before=={path:path.read_bytes() for path in root.rglob('*') if path.is_file()}
        assert job==original_job and c.read(manifest)==original_job


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault',['call','name','source','proof','digest','size','symlink','conflicting_duplicate'])
def test_managed_exports_reject_unbound_or_changed_originals(tmp_path,monkeypatch,remote_registry,fault):
    launcher,root,job=prepared(tmp_path,monkeypatch)
    job=copy.deepcopy(job);item=job['direct_materials'][0];source=Path(item['source'])
    if fault=='call':
        parts=list(Path(item['path']).parts)
        assert parts[:5]==['inputs','parents','002','artifacts','experiments']
        assert parts[5]!='different-call'
        parts[5]='different-call';item['path']=Path(*parts).as_posix()
        assert not (c.workspace(root,3)/item['path']).exists()
    elif fault=='name':item['path']=str(Path(item['path']).with_name('not-the-collected-name'))
    elif fault=='source':
        outside=tmp_path/'private';outside.write_bytes(source.read_bytes());item['source']=str(outside)
    elif fault=='proof':
        seal=c.read(execution.record(root,2)/'result-identity.json');path=root/seal['observations']['path']
        value=c.read(path);value['managed_calls'][0]['outputs'][0]['name']='other-descriptor';c.write(path,value)
    elif fault=='digest':item['sha256']='f'*64
    elif fault=='size':item['size_bytes']+=1
    elif fault=='symlink':
        source.chmod(0o600);data=source.read_bytes();source.unlink();other=tmp_path/'same-bytes';other.write_bytes(data);source.symlink_to(other)
    elif fault=='conflicting_duplicate':
        conflict=dict(item);conflict['size_bytes']+=1
        assert conflict['path']==item['path'] and conflict!=item
        job['direct_materials'].append(conflict)
    c.write(root/'jobs/003.json',job)
    with pytest.raises((ValueError,OSError)):
        launcher.direct_materials(dict(scene_root=str(root)),c.workspace(root,3),root/'jobs/003.json')


def test_launcher_reads_full_control_job_and_streams_large_material_digest(tmp_path,monkeypatch):
    import json
    spec=importlib.util.spec_from_file_location('large_control_launcher',Path(__file__).resolve().parents[2]/'scripts/acceptance/rootless_cov_launcher.py')
    launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
    job=tmp_path/'job.json';value=dict(direct_materials=[],full_control_metadata='x'*(5*1024*1024))
    with job.open('w') as stream:json.dump(value,stream)
    assert json.loads(launcher.regular(job))==value
    asset=tmp_path/'asset.bin'
    with asset.open('wb') as stream:stream.truncate(33*1024*1024)
    digest=c.digest(asset)
    original=Path.read_bytes
    def no_large_read(path):
        if path==asset:raise AssertionError('Material digest must stream')
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',no_large_read)
    assert launcher.file_digest(asset)==digest
