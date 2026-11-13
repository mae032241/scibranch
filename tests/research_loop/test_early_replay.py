"""Early optional engineering feedback through real managed calls and isolated leaves.

Written for later supervised execution; no scientific models, private data or full
training. The fixture trains a constant predictor through the public runner shape.
"""

from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop.protocol import CURRENT

pytestmark = pytest.mark.process_e2e


# These exact nodes inherit process_e2e, but inspect inert requests/argv only;
# Git, delivery leaves, namespace probes and process execution are mocked.


def _replay_isolate_argv(tmp_path,monkeypatch):
    from cov_execution import delivery as cov_delivery
    root=tmp_path/'argv';stage=root/'stage';(stage/'inputs').mkdir(parents=True);(stage/'scratch').mkdir()
    (stage/'inputs/source.py').write_bytes(b'')
    source=root/'current-export/source.py';source.parent.mkdir();source.write_bytes(b'fixed Git code view')
    adapter=root/'delivery.py';adapter.write_text('# inert adapter, never executed\n')
    python=Path(sys.executable).absolute();runtime=Path(sys.executable).resolve()
    scope=dict(handoff_protocol=CURRENT,step=1,role='W',job_ref=dict(path=str(root/'jobs/001.json')),
        execution_key=dict(path=str(root/'native-intent.json')),mechanism_ref=None)
    request=dict(phase='replay',scope=scope,stage=str(stage),deadline_boottime=110.,
        config=dict(python=str(python)),inputs=[dict(path=str(runtime),size_bytes=runtime.stat().st_size,
            invoked_path=str(python),label='runtime-python')],
        object_refs={'source.py':dict(path=str(source),size_bytes=source.stat().st_size)})
    calls=[]
    monkeypatch.setattr(cov_delivery.shutil,'which',lambda name:'/argv-only/bwrap')
    monkeypatch.setattr(cov_delivery.os,'execv',lambda binary,argv:calls.append((binary,list(argv))))
    def forbidden(*args,**kwargs):pytest.fail('Replay isolate argv test reached a subprocess')
    for name in ('Popen','run','call'):monkeypatch.setattr(cov_delivery.subprocess,name,forbidden)
    return cov_delivery,request,adapter,source,calls


def test_cov_git_replay_isolate_overlays_current_code_read_view_after_empty_stage(tmp_path,monkeypatch):
    module,request,adapter,source,calls=_replay_isolate_argv(tmp_path,monkeypatch)
    module.isolate(request,str(adapter));assert len(calls)==1
    binary,argv=calls[0];stage=Path(request['stage'])
    assert binary=='/argv-only/bwrap'
    bindings=[argv[index:index+3] for index,arg in enumerate(argv) if arg in ('--bind','--ro-bind')]
    stage_binding=['--ro-bind',str(stage/'inputs'),'/inputs'];code_binding=['--ro-bind',str(source),'/inputs/source.py']
    assert stage_binding in bindings and code_binding in bindings
    assert argv.index(str(source))>argv.index(str(stage/'inputs'))
    assert (stage/'inputs/source.py').read_bytes()==b'' and source.read_bytes()==b'fixed Git code view'
    assert argv[-5:]==[str(Path(sys.executable).absolute()),'-B','-I','/delivery.py','--leaf']


@pytest.mark.parametrize('damage',['size','missing','symlink'])
def test_cov_git_replay_isolate_refuses_nonexact_source_before_exec(tmp_path,monkeypatch,damage):
    module,request,adapter,source,calls=_replay_isolate_argv(tmp_path,monkeypatch)
    module.isolate(request,str(adapter));assert len(calls)==1;calls.clear()
    if damage=='size':request['object_refs']['source.py']['size_bytes']+=1
    elif damage=='missing':source.unlink()
    else:
        original=source.with_name('original.py');source.rename(original);source.symlink_to(original)
    with pytest.raises(ValueError):module.isolate(request,str(adapter))
    assert calls==[] and (Path(request['stage'])/'inputs/source.py').read_bytes()==b''
