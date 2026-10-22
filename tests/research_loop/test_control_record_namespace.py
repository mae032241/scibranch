"""Host record relocation preserves exact task ownership and frozen old paths."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c, experiment_client
from cov_execution import lineage as cov_lineage
from scidiscovery.research_loop import experiments as e, instance_archive, instance_owner


def task(root, step=2, version=1):
    job=dict(step=step, role='W')
    if version is not None:
        job['worker_view_version']=version
    path=root/'jobs'/f'{step:03d}.json'
    c.write(path,job)
    directory=experiment_client.record_directory(root,step)
    config=dict(record_dir=str(directory),scope=dict(step=step,role='W',job_sha256=c.digest(path)))
    return path,config


@pytest.mark.parametrize('version',[None,1])
def test_record_namespace_preserves_exact_job_and_owner(tmp_path,version):
    path,config=task(tmp_path,version=version)
    original=path.read_bytes()
    expected=(tmp_path/'host-runtime/experiment-control'/c.digest(path)/'records'
        if version==1 else tmp_path/'project/nodes/002/work/lineage')
    assert Path(config['record_dir'])==expected
    assert experiment_client.root_for_configuration(config)==tmp_path
    assert instance_owner.configuration_root(config)==tmp_path
    assert path.read_bytes()==original
    changed=dict(config,scope=dict(config['scope'],job_sha256='0'*64))
    with pytest.raises(instance_owner.files.ArchiveError,match='original job'):
        instance_owner.configuration_root(changed)


@pytest.mark.parametrize('damage',['task_directory','scope','relative','symlink'])
def test_managed_namespace_never_falls_back_for_changed_identity(tmp_path,damage):
    _,config=task(tmp_path)
    if damage=='task_directory':
        config['record_dir']=str(tmp_path/'host-runtime/experiment-control'/('f'*64)/'records')
    elif damage=='scope':
        config['scope']['step']=True
    elif damage=='relative':
        config['record_dir']='host-runtime/experiment-control/'+('a'*64)+'/records'
    else:
        actual=tmp_path/'actual';actual.mkdir()
        link=tmp_path/'link';link.symlink_to(actual,target_is_directory=True)
        config['record_dir']=str(link/'host-runtime/experiment-control'/('a'*64)/'records')
    with pytest.raises(e.IntegrityError):
        experiment_client.root_for_configuration(config,allow_standalone=True)


def test_standalone_commands_keep_no_managed_owner(tmp_path):
    config=dict(record_dir=str(tmp_path/'work/lineage'))
    assert instance_owner.configuration_root(config) is None
    with pytest.raises(e.IntegrityError):
        experiment_client.root_for_configuration(config)


def test_archive_includes_new_and_frozen_legacy_records_and_leases(tmp_path):
    _,old=task(tmp_path,step=1,version=None)
    _,new=task(tmp_path,step=2)
    for config in (old,new):
        records=Path(config['record_dir']);records.mkdir(parents=True)
        c.write(records/'calls/original/accepted.json',{})
        for name in ('node.lock','tcad-submission-original.lock'):
            parts=(records/name).relative_to(tmp_path).parts
            assert instance_archive._excluded(tmp_path,parts)=='managed_records_lease'
    assert set(instance_archive._managed_roots(tmp_path))=={Path(old['record_dir']),Path(new['record_dir'])}
    unknown=tmp_path/'host-runtime/experiment-control'/('f'*64)/'records'
    unknown.mkdir(parents=True)
    with pytest.raises(instance_archive.files.ArchiveError,match='Unknown managed records'):
        list(instance_archive._managed_roots(tmp_path))


def test_live_progress_uses_the_exact_job_selected_control_namespace(tmp_path,monkeypatch):
    _,config=task(tmp_path)
    monkeypatch.setattr(cov_lineage,'_progress',lambda value,scope:value)
    value=cov_lineage.progress(dict(root=str(tmp_path),step=2,scope=config['scope'],
        config=dict(calls='project/nodes',evaluation_key='fixture')))
    assert value['record_dir']==config['record_dir']
    with pytest.raises(e.IntegrityError):
        cov_lineage.progress(dict(root=str(tmp_path),step=2,scope=config['scope'],
            config=dict(calls='unrelated/nodes',evaluation_key='fixture')))
