"""Same-call Cov recovery waits for the real host completion writer."""
import json
from pathlib import Path
import sys
import threading

import pytest

from cov_execution import receipt
from execution_fixtures import execution_registry
from scidiscovery.operations.execution import LocalFileDescriptor
from scidiscovery.research_loop import experiments, worker_execution_bridge as bridge
from test_cov_lineage import wrapper


@pytest.mark.process_e2e
@pytest.mark.parametrize('returncode',[0,1],ids=['success','failure'])
@pytest.mark.parametrize('publication',['complete','receipt-only','terminal-only'])
def test_cov_query_waits_for_finish_and_recovers_only_after_writer_exit(wrapper,monkeypatch,publication,returncode):
    config=json.loads(wrapper.config.read_bytes())
    writer=experiments.load(config,path=wrapper.config)
    reader=experiments.load(config,path=wrapper.config)
    assert writer is not reader
    source=wrapper.script('finish-source')
    payload=LocalFileDescriptor(name='finish-call',local_path=str(source),
        size_bytes=source.stat().st_size,media_type='application/json')
    accepted=writer.records.accept(payload,writer.acceptance_metadata(payload,new=True),config['execution_interface'])
    call_id=accepted['call_id'];directory=writer.records.directory(call_id)
    entered=threading.Event();release=threading.Event()
    launches=[];outcomes=[];errors=[]

    def launch(configuration,original,command,cwd):
        assert original==accepted
        launches.append(original['call_id'])
        wrapper.runs.mkdir()
        row=dict(run_id=1,script=accepted['source_script'],ok=returncode==0,
            wall_seconds=.01)
        if returncode==0:
            model=wrapper.runs/'model_run1.pkl';model.write_bytes(b'inert completion artifact')
            row.update(model_path=str(model),val_score=.5)
        (wrapper.runs/'results.jsonl').write_bytes(experiments.encoded(row))

        def finish(actual,cleanup,transport_complete):
            assert actual==returncode and cleanup is transport_complete is True
            assert (directory/'terminal.json').is_file()
            entered.set()
            assert release.wait(10),'Query did not release the finish barrier'
            if publication!='terminal-only':
                receipt.publish(directory/'receipt.json',receipt.encoded(receipt.envelope(accepted,row)),
                    pending=directory/'receipt.pending')
            if publication!='complete':
                raise OSError('injected host completion writer exit')

        # Execute only an inert bounded interpreter; no model or runner is loaded.
        return [sys.executable,'-c',f'raise SystemExit({returncode})'],None,finish

    def submit():
        token=bridge._LOCAL_LAUNCH.set(launch)
        try:
            writer.dispatch_lease(accepted)
            outcomes.append(writer.submit(payload))
        except BaseException as error:
            errors.append(error)
        finally:
            bridge._LOCAL_LAUNCH.reset(token)

    thread=threading.Thread(target=submit,daemon=True)
    thread.start()
    try:
        assert entered.wait(10),errors
        terminal=(directory/'terminal.json').read_bytes()
        assert not (directory/'receipt.json').exists()
        assert not (directory/'completed.json').exists()
        # A second adapter reaches the real status/result seam while finish owns
        # publication. A stopped scientific child cannot license recovery yet.
        details=reader.status_details(call_id)
        assert details['state']=='unknown' and details['candidate_id'] is None
        assert reader.result(call_id)['status']=='unknown'
        assert not (directory/'completed.json').exists()
        assert not (directory/'receipt.json').exists()
        assert (directory/'terminal.json').read_bytes()==terminal
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive()
    if publication=='complete':
        assert not errors
        assert outcomes==[(call_id,'succeeded' if returncode==0 else 'failed')]
    else:
        assert not outcomes
        assert len(errors)==1 and isinstance(errors[0],OSError)
        assert str(errors[0])=='injected host completion writer exit'
        assert not (directory/'completed.json').exists()
    monkeypatch.setattr(reader,'command',lambda *_:pytest.fail('Recovery must not execute the call'))
    result=reader.result(call_id)
    assert result['status']==('known_success' if returncode==0 else 'known_failed')
    if publication=='terminal-only':
        assert result['candidate_id'] is result['run_id'] is result['model'] is None
        assert result['result']['diagnostic']=='cov_receipt_unavailable'
        assert result['returncode']==returncode
        assert not (directory/'receipt.json').exists()
    else:
        assert result['candidate_id']=='run-1' and result['run_id']==1
        if returncode==0:
            assert Path(result['model']['path']).read_bytes()==b'inert completion artifact'
        else:
            assert result['model'] is None
    completed=(directory/'completed.json').read_bytes()
    assert reader.result(call_id)==result
    details=reader.status_details(call_id)
    assert details['state']==('succeeded' if returncode==0 else 'failed')
    assert details['candidate_id']==result['candidate_id']
    assert (directory/'terminal.json').read_bytes()==terminal
    assert (directory/'completed.json').read_bytes()==completed
    assert launches==[call_id]
