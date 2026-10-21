"""Cold same-host framework recovery; no subprocess, native model or installation."""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys

import pytest

from scidiscovery.research_loop import (budget, cli, contracts as c, controller,
    instance_archive as archive, instance_owner as owner, instance_science as science)
from test_history_handoff import finish


@pytest.fixture
def cold(tmp_path, monkeypatch):
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('Preserve a complete sealed scientific instance.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable],max_tokens=2000,max_seconds=60,max_steps=4,
        model='fixture',reasoning_effort='high',search_policy=None,experiment_mode='external',node_budget_mode='soft')
    seal=c.seal_result
    def cleaned(root,step):
        path=c.host_record(c.workspace(root,step))/'native-outcome.json'
        c.write(path,dict(c.read(path),cleanup='observed_group_cleaned'))
        return seal(root,step)
    monkeypatch.setattr(c,'seal_result',cleaned)
    finish(root,1,'H',[],action='experiment',files={'result.txt':'Exact sealed scientific bytes.\n'})
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'paused','Cold fixture explicit inactive boundary')
    anchor=tmp_path/'anchor';anchor.mkdir()
    return root,anchor


def test_cold_checkpoint_preserves_pending_node_configuration(cold, tmp_path):
    from scidiscovery.research_loop import configuration_updates, research_config
    root, anchor = cold
    document = research_config.to_document(root, c.settings(root))
    document['agent']['model'] = 'next-model'
    configuration_updates.submit(root, document, expected_revision=0)
    original = (root / 'host-control/configuration/revisions/000001.json').read_bytes()
    archive.checkpoint_enroll(root, anchor)
    package = tmp_path / 'configuration-checkpoint'
    archive.checkpoint_export(root, package)
    root.rename(tmp_path / 'previous-instance')
    archive.checkpoint_restore(root, package)
    assert (root / 'host-control/configuration/revisions/000001.json').read_bytes() == original
    state = configuration_updates.status(root)
    assert state['pending'] is True
    assert state['active_settings']['model'] == 'fixture'
    assert state['settings']['model'] == 'next-model'
    assert c.settings(root, c.read(root / 'jobs/001.json'))['model'] == 'fixture'


def test_public_cold_checkpoint_restores_complete_state_twice(cold,tmp_path,capsys,monkeypatch):
    root,anchor=cold
    assert cli.main(['checkpoint-enroll','--root',str(root),'--anchor-directory',str(anchor)])==0
    assert json.loads(capsys.readouterr().out)['status']=='checkpoint_enrolled'
    seal=c.host_record(c.workspace(root,1))/'result-identity.json';sealed=seal.read_bytes()
    settings=(root/'settings.json').read_bytes();started=(root/'host-started.json').read_bytes()
    original_ledger={p.name:p.read_bytes() for p in (root/'control-ledger').glob('*.json')}
    for number in (1,2):
        package=tmp_path/f'checkpoint-{number}'
        assert cli.main(['checkpoint-export','--root',str(root),'--output',str(package)])==0
        assert json.loads(capsys.readouterr().out)['frozen'] is True
        with pytest.raises(archive.files.ArchiveError,match='frozen|superseded'):
            with controller._writer(root):pass
        with pytest.raises(archive.files.ArchiveError,match='never deletes'):
            archive.checkpoint_restore(root,package)
        root.rename(tmp_path/f'source-generation-{number}')
        with pytest.raises(archive.files.ArchiveError,match='original root'):
            owner.require_owner(tmp_path/f'source-generation-{number}')
        if number==2:
            # Only the observed clock changes. The real external anchor and
            # checkpoint/transfer verifier must authorize the new boot segment.
            stamp=budget.clock_stamp()
            monkeypatch.setattr(budget,'clock_stamp',lambda:dict(stamp,boot_id='fixture-cold-next-boot',at_boottime=3.0))
        assert cli.main(['checkpoint-restore','--root',str(root),'--package',str(package)])==0
        result=json.loads(capsys.readouterr().out)
        assert result['generation']==number+1 and result['paused'] is True
        assert (root/'settings.json').read_bytes()==settings and seal.read_bytes()==sealed
        assert (root/'host-started.json').read_bytes()==started
        current_ledger={p.name:p.read_bytes() for p in (root/'control-ledger').glob('*.json')}
        assert {name:current_ledger[name] for name in original_ledger}==original_ledger
        if number==1:
            assert current_ledger==original_ledger
            consumed=budget.snapshot(root)['consumed_seconds']
        else:
            appended=[json.loads(raw) for name,raw in current_ledger.items() if name not in original_ledger]
            assert len(appended)==1 and appended[0]['kind']=='clock' and appended[0]['phase']=='paused'
            assert appended[0]['reanchor']['transfer']['path'].startswith('host-runtime/restore/')
            view=budget.snapshot(root)
            assert view['consumed_seconds']==pytest.approx(consumed)
            assert view['elapsed_seconds'] is None and view['unmeasured_paused_gap'] is True
        member=next(row for row in science.materials(root,1)['items'] if row['name']=='result.txt')
        original,_=science.artifact(root,1,member['index'])
        with original:assert original.stream.read()==b'Exact sealed scientific bytes.\n'
        receipts=list((root/'host-runtime/restore').glob('*/transfer.json'))
        assert len(receipts)==number
        for path in receipts:
            from scidiscovery.research_loop import qualification as q
            value=c.read(path);owner.read_transition(root,value['checkpoint_ref'],q._ref(root,path))
        assert archive.checkpoint_restore(root,package)['generation']==number+1
        with controller._writer(root):pass
    # Repeating a completed restore after real control advancement is a pure
    # acknowledgment; it must not restore an old pause, ledger or clock bridge.
    with controller._writer(root):
        (root/'pause.request').unlink()
        budget.transition(root,'advance','Fixture control advancement, no native launch')
    current={p.name:p.read_bytes() for p in (root/'control-ledger').glob('*.json')}
    again=archive.checkpoint_restore(root,package)
    assert again['status']=='checkpoint_restore_already_completed' and again['paused'] is False
    assert {p.name:p.read_bytes() for p in (root/'control-ledger').glob('*.json')}==current


def test_cold_transfer_blocks_live_effect_and_rejects_plain_archive(cold,tmp_path,monkeypatch):
    root,anchor=cold
    # Ordinary instances keep their existing writer and archive behavior.
    with controller._writer(root):pass
    ordinary=tmp_path/'readonly';archive.archive_export(root,ordinary)
    with pytest.raises((archive.files.ArchiveError,FileNotFoundError)):
        archive.checkpoint_restore(root,ordinary)
    durable=owner.files.durable_json;interrupted=False;settings=(root/'settings.json').read_bytes()
    def enrollment_crash(path,value):
        nonlocal interrupted
        if path==root/owner.BINDING and not interrupted:
            interrupted=True
            raise RuntimeError('Simulated marker-before-binding interruption')
        return durable(path,value)
    monkeypatch.setattr(owner.files,'durable_json',enrollment_crash)
    with pytest.raises(RuntimeError,match='marker-before-binding'):
        archive.checkpoint_enroll(root,anchor)
    with pytest.raises(archive.files.ArchiveError,match='binding is missing'):
        with controller._writer(root):pass
    assert len(list(anchor.iterdir()))==1
    archive.checkpoint_enroll(root,anchor)
    assert len(list(anchor.iterdir()))==1 and (root/'settings.json').read_bytes()==settings
    with owner.lease(root):
        with pytest.raises(archive.files.ArchiveError,match='outside an effect lease'):
            archive.checkpoint_export(root,tmp_path/'blocked')
    assert not (tmp_path/'blocked').exists()
    with controller._writer(root):pass


def test_restore_dependency_failure_stays_inactive_then_same_transaction_recovers(cold,tmp_path,monkeypatch):
    root,anchor=cold;archive.checkpoint_enroll(root,anchor)
    package=tmp_path/'checkpoint';archive.checkpoint_export(root,package)
    root.rename(tmp_path/'source')
    dependencies=archive._restore_dependencies
    def missing(settings):raise archive.files.ArchiveError('Required current dependency unavailable')
    monkeypatch.setattr(archive,'_restore_dependencies',missing)
    with pytest.raises(archive.files.ArchiveError,match='dependency unavailable'):
        archive.checkpoint_restore(root,package)
    with pytest.raises(archive.files.ArchiveError,match='frozen|superseded'):
        with controller._writer(root):pass
    monkeypatch.setattr(archive,'_restore_dependencies',dependencies)
    job=root/'jobs/001.json';original=job.read_bytes();mode=job.stat().st_mode&0o777
    job.chmod(0o600);job.write_bytes(b'changed control original')
    with pytest.raises(archive.files.ArchiveError,match='Pending restore original changed'):
        archive.checkpoint_restore(root,package)
    job.write_bytes(original);job.chmod(mode)
    assert archive.checkpoint_restore(root,package)['status']=='checkpoint_restored'


def test_cold_restore_wrong_host_missing_anchor_and_tamper_never_advance(cold,tmp_path,monkeypatch):
    root,anchor=cold;archive.checkpoint_enroll(root,anchor)
    package=tmp_path/'checkpoint';archive.checkpoint_export(root,package)
    root.rename(tmp_path/'source')
    host=owner._host;monkeypatch.setattr(owner,'_host',lambda:'different-host')
    with pytest.raises(archive.files.ArchiveError,match='same host'):
        archive.checkpoint_restore(root,package)
    monkeypatch.setattr(owner,'_host',host)
    directory=anchor/next(anchor.iterdir()).name;directory.rename(tmp_path/'lost-anchor')
    with pytest.raises(archive.files.ArchiveError,match='anchor is unavailable'):
        archive.checkpoint_restore(root,package)
    (tmp_path/'lost-anchor').rename(directory)
    blob=next((package/'payload/blobs').iterdir());blob.chmod(0o600);blob.write_bytes(b'changed')
    with pytest.raises(archive.files.ArchiveError,match='blob changed'):
        archive.checkpoint_restore(root,package)
    assert not root.exists()


@pytest.mark.process_e2e
def test_external_owner_lease_fences_cold_checkpoint(cold,tmp_path):
    root,anchor=cold;archive.checkpoint_enroll(root,anchor)
    code=('import sys\nfrom pathlib import Path\n'
          'from scidiscovery.research_loop.instance_owner import lease\n'
          'with lease(Path(sys.argv[1])):\n'
          ' print("owner-held",flush=True)\n'
          ' sys.stdin.readline()\n')
    process=subprocess.Popen([sys.executable,'-c',code,str(root)],stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True,
        env=dict(os.environ,PYTHONPATH=str(Path(owner.__file__).resolve().parents[2])))
    try:
        assert select.select([process.stdout],[],[],5)[0], 'Owner child did not become ready'
        assert process.stdout.readline()=='owner-held\n'
        with pytest.raises(archive.files.ArchiveError,match='effect is still active'):
            archive.checkpoint_export(root,tmp_path/'blocked')
        assert not (tmp_path/'blocked').exists()
    finally:
        try:
            stdout,stderr=process.communicate('release\n',timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL)
            stdout,stderr=process.communicate(timeout=5)
    assert process.returncode==0,(process.returncode,stdout,stderr)
    assert archive.checkpoint_export(root,tmp_path/'after-release')['status']=='checkpoint_exported'
