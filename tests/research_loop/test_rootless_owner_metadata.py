"""Exact readonly namespace originals; no implicit cold-owner lease bypass."""
from pathlib import Path
import subprocess
import sys

import pytest

from scidiscovery.research_loop import contracts as c, instance_owner as owner
from test_cold_checkpoint import cold
from test_rootless_fragments import launcher


def specification(root):
    return dict(scene_root=str(root), runs_dir=str(root/'runs'), submission_dir=str(root/'submission'),
        public_root=c.settings(root)['public_root'], native='/usr/bin/true',
        code_mode_host={'path':'/usr/bin/true'},runtime_paths=[],profile='owner-metadata',
        task_python=sys.executable,scid_python=sys.executable,model='fixture',reasoning_effort='high')


def test_launcher_only_shares_current_job_and_settings_identity(launcher,cold,monkeypatch):
    root,_=cold;work=c.workspace(root,1);home=root/'native-homes/001';s=specification(root)
    monkeypatch.setenv('THIN_HWA_ROLE','H')
    monkeypatch.setenv('THIN_HWA_JOB_MANIFEST',str(root/'jobs/001.json'))
    expected=(root/'jobs/001.json',root/'settings-identity.json')
    assert launcher.owner_metadata(s,work)==expected
    launcher.prepare_home(s,work,home)
    import tomllib
    grants=tomllib.loads((home/'config.toml').read_text())['permissions']['owner-metadata']['filesystem']
    assert all(grants[str(path)]=='read' for path in expected)
    assert all(str(path) not in grants for path in (root,root/'jobs',root/'settings.json',root/'control-ledger'))
    command=launcher.outer(s,work,home,['/usr/bin/true'])
    bindings=[command[i+1:i+3] for i,arg in enumerate(command) if arg in ('--ro-bind','--bind')]
    assert all([str(path),str(path)] in bindings for path in expected)
    assert all([str(path),str(path)] not in bindings for path in (root,root/'jobs',root/'settings.json',root/'control-ledger'))


@pytest.mark.parametrize('damage',['missing','corrupt'])
def test_missing_or_corrupt_identity_rejected_before_native_home(launcher,cold,damage):
    root,_=cold;work=c.workspace(root,1);home=root/'native-homes/new';s=specification(root)
    path=root/'settings-identity.json'
    if damage=='missing':path.unlink()
    else:path.write_text('{}')
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises((ValueError,OSError)):
        launcher.prepare_home(s,work,home)
    assert not home.exists()
    assert before=={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('state',['active','fenced','missing_binding','corrupt_binding'])
def test_cold_owner_is_validated_and_rejected_before_namespace(launcher,cold,state):
    root,anchor=cold;binding=owner.enroll(root,anchor)
    if state=='fenced':
        path=Path(binding['anchor'])/'owner.json';c.write(path,dict(c.read(path),state='frozen'))
    elif state=='missing_binding':(root/owner.BINDING).unlink()
    elif state=='corrupt_binding':(root/owner.BINDING).write_text('{}')
    work=c.workspace(root,1);home=root/'native-homes/new';s=specification(root)
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    expected='cold_enrolled_owner_unsupported' if state=='active' else 'frozen|superseded|binding is missing|unavailable'
    with pytest.raises((ValueError,owner.files.ArchiveError),match=expected):
        launcher.prepare_home(s,work,home)
    assert not home.exists()
    assert before=={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.process_e2e
@pytest.mark.parametrize('action,arguments',[('capabilities',['--start','0']),
    ('describe',['--key','cov_single_source','--output','work/contract with spaces.json'])])
def test_native_contract_discovery_forwards_exact_argv(launcher,tmp_path,action,arguments):
    executable=tmp_path/'scid python'
    executable.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@"\nexit 7\n');executable.chmod(0o755)
    wrapper=tmp_path/'exp'
    wrapper.write_text(launcher.exp_wrapper(dict(scid_python=str(executable),task_python='/never-public-harness'),tmp_path))
    wrapper.chmod(0o755)
    result=subprocess.run([str(wrapper),action,*arguments],capture_output=True,text=True)
    assert result.returncode==7
    assert result.stdout.splitlines()==['-B','-I','-m','scidiscovery.research_loop.experiments',
        '--config',str(tmp_path/'work/lineage-config.json'),action,*arguments]
    assert result.stderr==''
