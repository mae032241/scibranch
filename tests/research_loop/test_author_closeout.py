"""Bounded author recovery preserves terminal calls and denies native effects."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import author_closeout,budget,contracts as c,native
from scidiscovery.research_loop.experiment_client import record_directory
from test_cold_checkpoint import cold
from test_cov_delivery_lineage import managed_scene
from test_operator_usage_reserve import unknown_original
from test_rootless_fragments import launcher
from test_rootless_owner_metadata import specification


@pytest.mark.process_e2e
def test_terminal_facts_use_retained_transport_without_an_adapter(tmp_path,execution_registry,monkeypatch):
    root,_,first,*_=managed_scene(tmp_path)
    from scidiscovery.research_loop import experiments
    monkeypatch.setattr(experiments,'load',lambda *a,**kw:pytest.fail('No backend or adapter loading for author closeout'))
    calls=record_directory(root,2)/'calls'
    before={p:p.read_bytes() for p in calls.rglob('*') if p.is_file()}
    assert before and len(list(calls.glob('*/accepted.json')))==2
    facts=author_closeout.terminal_facts(root,2)
    assert len(facts['calls'])==2
    assert facts['calls'][0]['candidate_id']==first['candidate_id']
    assert all(row['status']=='known_success' for row in facts['calls'])
    assert all('val_score' not in row and 'audit_pass' not in row for row in facts['calls'])
    assert before=={p:p.read_bytes() for p in calls.rglob('*') if p.is_file()}
    accepted=next(calls.glob('*/accepted.json'));accepted.chmod(0o600)
    c.write(accepted,dict(c.read(accepted),call_id='another-call'))
    with pytest.raises(ValueError,match='another frozen execution interface'):
        author_closeout.terminal_facts(root,2)


@pytest.mark.process_e2e
@pytest.mark.parametrize('action',['run','compile','submit','query','collect','cancel'])
def test_closeout_native_wrapper_denies_effects_before_any_call(launcher,tmp_path,action):
    wrapper=tmp_path/'exp'
    wrapper.write_text(launcher.exp_wrapper(dict(_read_only_closeout=True),tmp_path));wrapper.chmod(0o755)
    result=subprocess.run([str(wrapper),action,'--script','never-execute.py'],capture_output=True,text=True)
    assert result.returncode==2
    reply=json.loads(result.stdout)
    assert reply['status']=='known_not_started' and reply['accepted'] is False and 'call_id' not in reply
    assert list(tmp_path.iterdir())==[wrapper]


def test_author_start_requires_positive_actual_shared_balance(unknown_original):
    root,record,_=unknown_original
    receipt=dict(pid=999999999,start_ticks=1,pgid=999999999,sid=999999999,ppid=1,
        boot_id=budget.clock_stamp()['boot_id'],task_ref=c.record_ref(root/'jobs/001.json'))
    for name in ('runner-process.json','native-process.json'):c.write(record/name,receipt)
    meter=native.usage(root)
    assert meter['observed_tokens']==meter['accounted_tokens']==120 and meter['unknown_steps']==[1]
    budget.amend(root,max_tokens=meter['observed_tokens'],authorization_note='Reported usage has reached its actual ceiling.')
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='Finite author closeout and delivery reserve exceed remaining authorization'):
        author_closeout.start(root,1,max_tokens=10,max_seconds=10,authorization_note='Explicit bounded author closeout')
    assert before=={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.process_e2e
def test_real_closeout_namespace_keeps_originals_readonly_and_artifacts_writable(launcher,cold,monkeypatch):
    if shutil.which('bwrap') is None:pytest.skip('Actual bwrap namespace required')
    root,_=cold;work=c.workspace(root,1);home=root/'native-homes/author-context';current=root/'native-evidence/001/continuations/001'
    current.mkdir(parents=True);c.write(current/'native-intent.json',dict(fixture='new-author-attempt'))
    (work/'artifacts').mkdir(exist_ok=True)
    public=Path(c.settings(root)['public_root']);(public/'environment/harness').mkdir(parents=True)
    (public/'instruction.md').write_text('Readonly closeout namespace fixture; no model.')
    for name in ('exp_cli.py','runner.py','train.csv','val.csv','meta.json'):
        (public/'environment/harness'/name).write_text('fixture')
    (public/'environment/harness/exp_cli.py').write_text('from pathlib import Path\nPath("/opt/exp/runs/submitted.json").write_text("forbidden")\n')
    s=dict(specification(root),_read_only_closeout=True,_author_closeout=False,_closeout_record=str(current),
        runtime_paths=[sys.base_prefix,str(Path(sys.executable).parent.parent)])
    monkeypatch.setenv('THIN_HWA_ROLE','H');monkeypatch.setenv('THIN_HWA_JOB_MANIFEST',str(root/'jobs/001.json'))
    launcher.prepare_home(s,work,home)
    c.write(root/'host-runtime/lineage-001.json',dict(fixture='readonly original config'))
    (work/'work/lineage-config.json').touch()
    calls=work/'work/lineage/calls/original';calls.mkdir(parents=True)
    (calls/'accepted.json').write_text('Retained call fixture')
    before={p:p.read_bytes() for p in (root/'native-evidence/001').iterdir() if p.is_file()}
    code=r'''from pathlib import Path
import json,subprocess,sys
work,root,current,wrapper=map(Path,sys.argv[1:])
for path in (work/'work/new-call',work/'work/lineage/calls/original/accepted.json',root/'runs/submitted.json',root/'submission/model.pkl',root/'native-evidence/001/native-outcome.json'):
 try:path.write_text('forbidden')
 except OSError:pass
 else:raise AssertionError('Original namespace unexpectedly writable: '+str(path))
(work/'artifacts/new-conclusion.md').write_text('Fresh author artifact')
(current/'result.pending.json').write_text('{}')
for action in ('run','compile','submit','query','collect','cancel'):
 result=subprocess.run([str(wrapper),action],capture_output=True,text=True)
 assert result.returncode==2 and json.loads(result.stdout)['accepted'] is False
raw=subprocess.run([sys.executable,'-I','/opt/exp/exp_cli.py','submit'],capture_output=True,text=True)
assert raw.returncode!=0 and 'Read-only file system' in raw.stderr
'''
    command=launcher.outer(s,work,home,[sys.executable,'-I','-c',code,str(work),str(root),str(current),str(home/'scid-bin/exp')])
    result=subprocess.run(command,capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
    assert all(p.read_bytes()==value for p,value in before.items())
    assert (calls/'accepted.json').read_text()=='Retained call fixture'
    assert (work/'artifacts/new-conclusion.md').is_file() and (current/'result.pending.json').is_file()


def test_author_digest_cache_reuses_only_the_same_file_generation(tmp_path,monkeypatch):
    path=tmp_path/'original';path.write_bytes(b'first')
    author_closeout._generation_digest.cache_clear()
    digest=c.digest;reads=[]
    def counted(p):
        reads.append(Path(p));return digest(p)
    monkeypatch.setattr(c,'digest',counted)
    expected=author_closeout._ref(tmp_path,path)
    author_closeout._check(tmp_path,expected)
    assert reads==[path]
    old=path.stat();path.write_bytes(b'other')
    os.utime(path,ns=(old.st_atime_ns,old.st_mtime_ns))
    with pytest.raises(ValueError,match='original changed'):
        author_closeout._check(tmp_path,expected)
    assert reads==[path,path]
    replacement=tmp_path/'replacement';replacement.write_bytes(b'other')
    os.utime(replacement,ns=(old.st_atime_ns,old.st_mtime_ns));replacement.replace(path)
    with pytest.raises(ValueError,match='original changed'):
        author_closeout._check(tmp_path,expected)
    assert reads==[path,path,path]
    path.unlink();path.symlink_to(replacement)
    with pytest.raises(ValueError,match='original regular file'):
        author_closeout._check(tmp_path,expected)
    assert reads==[path,path,path]
    maxsize=author_closeout._generation_digest.cache_info().maxsize
    assert isinstance(maxsize,int) and maxsize>0


@pytest.mark.parametrize('worker_view_version',[None,1])
def test_author_call_cache_still_enumerates_new_originals(tmp_path,worker_view_version):
    root=tmp_path;job=dict(step=1,role='W')
    if worker_view_version is not None:
        job['worker_view_version']=worker_view_version
    c.write(root/'jobs/001.json',job)
    directory=record_directory(root,1)/'calls/original'
    directory.mkdir(parents=True);(directory/'accepted.json').write_text('first')
    original=author_closeout._calls(root,1)
    assert author_closeout._calls(root,1)==original
    (directory/'new-original.json').write_text('new')
    assert author_closeout._calls(root,1)!=original
    refreshed=author_closeout._calls(root,1)
    added=directory.parent/'new-call';added.mkdir()
    (added/'accepted.json').write_text('another call')
    updated=author_closeout._calls(root,1)
    assert len(updated)==len(refreshed)+1
    assert author_closeout._ref(root,added/'accepted.json') in updated


def test_author_digest_cache_rejects_generation_change_during_hash(tmp_path,monkeypatch):
    path=tmp_path/'original';path.write_bytes(b'first')
    author_closeout._generation_digest.cache_clear()
    digest=c.digest
    def changed(p):
        value=digest(p);Path(p).write_bytes(b'other');return value
    monkeypatch.setattr(c,'digest',changed)
    with pytest.raises(ValueError,match='changed while hashing'):
        author_closeout._ref(tmp_path,path)
    assert author_closeout._generation_digest.cache_info().currsize==0
