"""Current isolated delivery failures and bounded host-only leaf output."""
import json
from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import audit_recovery, budget, contracts as c, delivery
from execution_fixtures import execution_registry
from test_cov_delivery_lineage import managed_scene
from test_delivery import accepted,closeout_decision


pytestmark = pytest.mark.process_e2e


def _leaf_files(root,phase,count=1):
    indexes=sorted((root/'host-delivery/leaf-output').glob(f'*-{phase}-*.json'))
    assert len(indexes)==count,indexes
    for path in indexes:
        index=c.read(path);stream=index['streams']['stderr']
        data=(path.parent/stream['path']).read_bytes()
        assert stream['kept_bytes']==len(data) and 'sha256' not in stream
        yield index,data


@pytest.mark.parametrize('phase',['resolve','audit','promote','confirm'])
def test_isolated_leaf_failure_keeps_exact_traceback_as_host_only_output(tmp_path,monkeypatch,phase):
    # U4: cov_delivery leaf stderr was discarded, so the closeout traceback had to be inferred.
    root,nodes,first,*_=managed_scene(tmp_path)
    original=delivery._call
    def tamper(spec,request,seconds,**kwargs):
        if request['phase']==phase:
            protocol=Path(request['stage'])/'inputs/scid-current-protocol'
            protocol.chmod(0o600);protocol.write_bytes(protocol.read_bytes()+b'\n# altered frozen original\n')
        return original(spec,request,seconds,**kwargs)
    monkeypatch.setattr(delivery,'_call',tamper)
    if phase=='resolve':
        with pytest.raises(delivery.DeliveryUnresolved,match='delivery_resolver_unavailable'):
            delivery.freeze_sources(root,nodes)
    else:
        refs=delivery.freeze_sources(root,nodes)
        _,_,bound=closeout_decision(root,refs,recommendation=first['candidate_id'])
        submit=accepted(root,bound);result=delivery.invoke(root,submit,20)
        if phase=='confirm':
            budget.action_outcome(root,submit,result);result=delivery.invoke(root,accepted(root,bound,'confirm'),10)
        assert result['status']==('engineering_error' if phase=='audit' else 'unknown')
        keys={'status','operation_id','candidate_id','artifact_ref','note','cleanup_confirmed'}
        if phase=='audit':
            assert result['failure_phase']=='audit'
            keys.add('failure_phase')
        else:
            assert 'failure_phase' not in result
        assert set(result)==keys
        assert 'Traceback' not in result['note']
    if phase=='audit':
        retries=list(audit_recovery.facts(root))
        assert len(retries)==1 and retries[0][1]['status']=='engineering_error'
        assert retries[0][1]['note']=='audit_process_failed'
    else:
        assert not list(audit_recovery.facts(root))
    for index,data in _leaf_files(root,phase,2 if phase=='audit' else 1):
        assert index['phase']==phase and index['returncode']!=0 and index['outcome']=='exited'
        assert b'Traceback' in data and b'Frozen current protocol original differs' in data
    for path in (root/'project').rglob('*'):
        if path.is_file():assert b'Frozen current protocol original differs' not in path.read_bytes()


def test_leaf_output_tail_is_bounded_and_does_not_change_the_reply(tmp_path):
    launcher=tmp_path/'noisy.py'
    launcher.write_text('import sys\nsys.stdin.read()\nsys.stderr.write("e"*100000+"END")\nprint("{}")\n')
    evidence=tmp_path/'host-delivery/leaf-output'
    value=delivery._call(dict(launcher_argv=[sys.executable,str(launcher)],adapter=str(launcher)),
        dict(phase='confirm',operation_id='a'*64),10,evidence=evidence)
    assert value=={}
    index=c.read(evidence/(('a'*64)+'-confirm-1.json'))
    stream=index['streams']['stderr']
    assert stream['total_bytes']==100003 and stream['kept_bytes']==delivery.MAX_LEAF_TAIL and stream['truncated']
    data=(evidence/stream['path']).read_bytes()
    assert len(data)==delivery.MAX_LEAF_TAIL and data.endswith(b'END')
    assert not (evidence/stream['path']).stat().st_mode&0o222
    assert delivery._call(dict(launcher_argv=[sys.executable,str(launcher)],adapter=str(launcher)),
        dict(phase='confirm',operation_id='a'*64),10,evidence=evidence)=={}
    assert (evidence/(('a'*64)+'-confirm-2.json')).is_file()
