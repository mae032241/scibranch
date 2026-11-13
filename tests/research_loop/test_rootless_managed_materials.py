"""Current managed originals preserve exact descriptor bindings without backend reads."""
import copy
from pathlib import Path

import pytest

from execution_fixtures import execution_registry, remote_registry
from test_nonlocal_execution_observation import case, run
from test_history_handoff import finish
from scidiscovery.research_loop import contracts as c, execution


def prepared(tmp_path, monkeypatch):
    root,job,config,cli=case(tmp_path,monkeypatch)
    # Identical bytes under distinct call/descriptor identities must stay distinct.
    for label in ('1','2'):run(root,cli,label)
    finish(root,2,'W',[1],preserve_native_intent=True,mechanism_findings=[dict(mechanism=label,basis='observed',finding='Bounded synthetic evidence.') for label in ('P','Q')])
    job=c.build_job(root,3,'A',[1,2])
    return root,job


@pytest.mark.process_e2e
def test_frozen_managed_outputs_mount_with_exact_descriptor_and_no_backend_reload(tmp_path,monkeypatch,remote_registry):
    from scidiscovery.research_loop import experiments, shared_input_views
    root,job=prepared(tmp_path,monkeypatch)
    monkeypatch.setattr(experiments,'load',lambda *a,**kw:pytest.fail('material validation must not query backend'))
    seal=c.read(execution.record(root,2)/'result-identity.json')
    observed=c.read(root/seal['observations']['path'])
    calls=observed['managed_calls']
    assert len(calls)==2 and len({call['call_id'] for call in calls})==2
    expected=[dict(source=str(root/output['snapshot']),
        path=f'inputs/parents/002/artifacts/experiments/{call["call_id"]}/artifacts/{output["name"]}',
        size_bytes=output['size_bytes'])
        for call in calls for output in call['outputs']]
    assert len(expected)==2 and sorted(job['direct_materials'],key=lambda row:row['path'])==sorted(expected,key=lambda row:row['path'])
    assert all(row in c._public_materials(seal['materials']) for row in expected)
    work=c.workspace(root,3)
    arguments=shared_input_views.mount_arguments(root,job,work,include_skills=False)
    mounts=[tuple(arguments[index:index+3]) for index,flag in enumerate(arguments)
        if flag=='--ro-bind']
    before={path:path.read_bytes() for path in root.rglob('*') if path.is_file()}
    # Validate the existing current scientific view against exact originals.
    assert job['worker_view_version']==1
    c._validate_inputs(root,work,job)
    assert len({row['path'] for row in expected})==2
    for row in expected:
        source=Path(row['source']);target=work/row['path']
        assert source.is_relative_to(root) and source.is_file() and not source.is_symlink()
        assert source.resolve()==source and not target.exists()
        assert ('--ro-bind',str(source),str(target)) in mounts
        assert source.read_bytes()==b'opaque remote package; never interpreted as local Python'
        assert source.stat().st_size==row['size_bytes']
        assert 'host-history' not in row['path']
    assert before=={path:path.read_bytes() for path in root.rglob('*') if path.is_file()}


@pytest.mark.process_e2e
def test_identical_material_reference_is_an_idempotent_read_without_new_mounts(tmp_path,monkeypatch,remote_registry):
    from scidiscovery.research_loop import experiments
    root,job=prepared(tmp_path,monkeypatch)
    original_job=copy.deepcopy(job)
    repeated=copy.deepcopy(job)
    repeated['direct_materials'].append(dict(repeated['direct_materials'][0]))
    assert repeated['direct_materials'][-1]==job['direct_materials'][0]
    manifest=root/'jobs/003.json'
    manifest_bytes=manifest.read_bytes()
    # Check an identical repeated reference without changing the accepted manifest.
    monkeypatch.setattr(experiments,'load',lambda *a,**kw:pytest.fail('identical reference must not query backend'))
    before={path:path.read_bytes() for path in root.rglob('*') if path.is_file()}
    for _ in range(2):
        c._validate_inputs(root,c.workspace(root,3),repeated)
        assert before=={path:path.read_bytes() for path in root.rglob('*') if path.is_file()}
        assert job==original_job and manifest.read_bytes()==manifest_bytes


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault',['call','name','source','proof','size','symlink','conflicting_duplicate'])
def test_managed_exports_reject_unbound_or_changed_originals(tmp_path,monkeypatch,remote_registry,fault):
    root,job=prepared(tmp_path,monkeypatch)
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
    elif fault=='size':item['size_bytes']+=1
    elif fault=='symlink':
        source.chmod(0o600);data=source.read_bytes();source.unlink();other=tmp_path/'same-bytes';other.write_bytes(data);source.symlink_to(other)
    elif fault=='conflicting_duplicate':
        conflict=dict(item);conflict['size_bytes']+=1
        assert conflict['path']==item['path'] and conflict!=item
        job['direct_materials'].append(conflict)
    c.write(root/'jobs/003.json',job)
    with pytest.raises((ValueError,OSError)):
        c._validate_inputs(root,c.workspace(root,3),job)
