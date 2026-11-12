"""Private resource attribution and small release-scanner regressions."""
import json
import os
from pathlib import Path
import runpy
import sys

import pytest

from scripts import compiled_worker_process_guard as process_guard
from scripts import run_tests as guard


def test_resource_attribution_is_one_bounded_identity_checked_sample(monkeypatch):
    root=process_guard.ProcessIdentity(10,'fixture-boot',1)
    stale=process_guard.ProcessIdentity(99,'fixture-boot',2)
    table={10:process_guard._ProcessRecord(root,1,1,10,10),
        99:process_guard._ProcessRecord(process_guard.ProcessIdentity(99,'fixture-boot',99),1,10000,99,99)}
    for number in range(20,40):
        identity=process_guard.ProcessIdentity(number,'fixture-boot',number)
        table[number]=process_guard._ProcessRecord(identity,10,number,10,10)
    reads=[]
    def snapshot():reads.append(True);return table
    monkeypatch.setattr(process_guard,'_process_table',snapshot)
    diagnostic={};identities,total=process_guard._descendant_rss_kib({root,stale},diagnostic=diagnostic)
    assert len(reads)==1 and stale not in identities and len(identities)==21
    assert total==1+sum(range(20,40))==diagnostic['observed_tree_rss_kib']
    assert diagnostic['process_count']==21 and diagnostic['omitted_count']==5
    assert len(diagnostic['processes'])==16 and diagnostic['sampled_at_unix']>0
    assert [row['resident_kib'] for row in diagnostic['processes']]==sorted(range(24,40),reverse=True)
    assert all(row['parent_pid']==10 and row['boot_id']=='fixture-boot' and row['process_group']==10 for row in diagnostic['processes'])
    assert 'hard quota' in diagnostic['limitation']
    assert 'full_reference' not in diagnostic


@pytest.mark.process_e2e
def test_actual_resource_stop_keeps_private_attribution_and_original_cleanup(tmp_path,monkeypatch):
    limits=json.loads(guard.DEFAULT_CONFIG.read_text())['limits']
    limits=dict(limits,observed_tree_rss_stop_bytes=1,timeout_seconds=5)
    monkeypatch.setattr(guard,'memory',lambda:dict(MemAvailable=100_000_000_000))
    output=tmp_path/'private-resource';output.mkdir();(output/'fixture-logs').mkdir()
    inherited=os.environ.get('SCID_TEST_LOCK_FD')
    with guard.serial_lock(None if inherited is None else int(inherited)) as lock:
        result=guard.run_one([sys.executable,'-B','-I','-c','import time;time.sleep(10)'],
            dict(os.environ),limits,output,lock.fileno())
    assert result['stop_reason']=='observed_tree_rss_limit' and result['cleanup_complete'] is True
    sample=result['memory_stop_sample'];assert sample['observed_tree_rss_kib']>0
    assert any(row['pid']==result['child_pid'] for row in sample['processes'])
    assert len(sample['processes'])<=16 and sample['omitted_count']>=0
    assert json.loads((output/'resource.json').read_text())['memory_stop_sample']==sample
    assert not Path(f'/proc/{result["child_pid"]}').exists()
    assert limits['observed_tree_rss_stop_bytes']==1


@pytest.mark.parametrize('filename,content,rejected',[
    ('safe.txt','ordinary harmless release fixture',False),
    ('safe.key','ordinary harmless release fixture',True),
    ('injected.txt','BEGIN '+'PRIVATE KEY',True),
])
def test_release_scanner_preserves_path_after_text_and_pattern_loops(tmp_path,filename,content,rejected):
    release=runpy.run_path(str(Path(__file__).resolve().parents[2]/'scripts/build_git_release.py'))
    (tmp_path/filename).write_text(content)
    if rejected:
        with pytest.raises(RuntimeError,match='release scan failed'):release['_scan_release'](tmp_path)
    else:release['_scan_release'](tmp_path)
