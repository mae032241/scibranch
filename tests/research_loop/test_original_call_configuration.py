"""An original managed call keeps its accepted interface across native turns."""
import sys

import pytest

from execution_fixtures import execution_registry, remote_registry
from test_composed_execution_results import composed_registry
from test_nonlocal_execution_observation import case, run
from scidiscovery.research_loop import budget, contracts as c, execution, experiments as e


def advance(root,executor,options):
    origin=execution.origin(root,2)
    job=c.read(root/'jobs/002.json')
    meter=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3)
    c.write(origin/'usage.json',meter)
    report=dict(budget.node_report(root,job,c.read(origin/'native-intent.json'),meter),usage_observed=True)
    c.write(origin/'native-outcome.json',dict(returncode=1,stop_reason='synthetic original turn ended',budget_slice=report))
    anchor=budget.accept_continuation(root,job,[origin])
    assert anchor['previous_tokens']==10
    later=origin/'continuations/001';later.mkdir(parents=True)
    c.write(later/'native-intent.json',dict(attempt=1,task_ref=c.record_ref(root/'jobs/002.json'),
        previous=execution.proof(origin),root_intent_ref=c.record_ref(origin/'native-intent.json'),budget_slice=anchor))
    path=c.workspace(root,2)/'work/current-config.json'
    return e.configure(root,2,executor,options,path),path


def projection(root,config):
    return root/'host-runtime/experiment-control'/f'{config["scope"]["step"]:03d}'/'interfaces/000.json'


@pytest.mark.process_e2e
@pytest.mark.parametrize('executor',['local_execution:command','cov_execution:experiment','remote_execution:fixture'])
def test_continuation_old_terminal_cli_uses_original_interface(tmp_path,monkeypatch,execution_registry,remote_registry,executor):
    options={}
    if executor.startswith('cov_'):
        from test_cov_lineage import BACKEND
        backend=tmp_path/'backend.py';backend.write_text(BACKEND)
        options=dict(receipt_channel='backend_file',contract_version=2,feedback_version=1,runs_root=str(tmp_path/'runs'),
            run_argv=[sys.executable,str(backend),'--runs-root',str(tmp_path/'runs')],parents=[],evaluation_key='fixture-public-v1')
    root,job,old,cli=case(tmp_path,monkeypatch,executor=executor,extra=options,resume=True)
    if executor.startswith('remote_'):
        result=run(root,cli,'P')
    elif executor.startswith('cov_'):
        script=c.workspace(root,2)/'work/source.json';script.write_text('{"mode":"success"}')
        result=cli('run','--script',script,'--new-plan','--mechanism','P')
    else:
        script=c.workspace(root,2)/'work/source.py';script.write_text('print("original local call")\n')
        result=cli('run','--script',script,'--new-plan','--mechanism','P')
    call=result['call_id'];directory=e.Records(old).directory(call)
    old_bytes={p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    if executor.startswith('local_'):options=dict(run_argv=[sys.executable])
    current,path=advance(root,executor,options)
    assert current['execution_interface']!=old['execution_interface']
    assert projection(root,old).read_bytes()==(execution.origin(root,2)/'experiment-config.json').read_bytes()
    assert e.original_configuration(current,call)==old
    for command in ('query','lookup'):
        assert cli(command,'--call-id',call,config_file=path)['state']=='succeeded'
    assert cli('collect','--call-id',call,config_file=path)['status']=='collected'
    assert cli('reconcile','--call-id',call,config_file=path)['status']=='known_success'
    assert cli('lineage-status',config_file=path)['counts']['known_success']==1
    assert all(p.read_bytes()==data for p,data in old_bytes.items())
    assert len(e.Records(current).calls())==1
    # New admission belongs to the current turn, never the resolved old adapter.
    if executor.startswith('remote_'):
        package=c.workspace(root,2)/'work/next';package.write_bytes(b'new current attempt')
        result=cli('run','--payload',package,'--new-plan','--mechanism','Q',config_file=path)
        assert e.Records(current).accepted(result['call_id'])['native_attempt']==current['native_attempt']


@pytest.mark.process_e2e
@pytest.mark.parametrize('terminal',['known_success','known_failed'])
def test_async_original_call_reconciles_without_collect_or_resubmit(tmp_path,monkeypatch,composed_registry,terminal):
    options=dict(collect=False,terminal_status=terminal)
    root,job,old,cli=case(tmp_path,monkeypatch,executor='composed_execution:fixture',extra=options,resume=True)
    call=run(root,cli,'P','unknown')['call_id']
    current,path=advance(root,'composed_execution:fixture',dict(options,terminal_status='known_failed' if terminal=='known_success' else 'known_success'))
    assert cli('query','--call-id',call,config_file=path)['state']=='running'
    assert cli('reconcile','--call-id',call,config_file=path,expected=2)['status']=='running'
    package=c.workspace(root,2)/'work/replay';package.write_bytes(b'not accepted while running')
    assert cli('run','--payload',package,'--new-plan','--mechanism','Q',config_file=path,expected=2)['call_id']==call
    assert len(e.Records(current).calls())==1
    e.load(old).complete(call)
    assert cli('query','--call-id',call,config_file=path)['state']==('succeeded' if terminal=='known_success' else 'failed')
    assert not (e.Records(old).directory(call)/'execution-observation.json').exists()
    assert cli('reconcile','--call-id',call,config_file=path)['status']==terminal
    observed=e.Records(old).directory(call)/'execution-observation.json';before=observed.read_bytes()
    assert cli('reconcile','--call-id',call,config_file=path)['status']==terminal
    assert observed.read_bytes()==before and len(e.Records(current).calls())==1
    assert cli('collect','--call-id',call,config_file=path,expected=2)['status']=='unavailable'


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault',['missing','scope','record','attempt','interface','implementation','accepted-attempt'])
def test_original_projection_missing_or_tampered_fails_closed(tmp_path,monkeypatch,remote_registry,fault):
    root,job,old,cli=case(tmp_path,monkeypatch,resume=True)
    call=run(root,cli,'P')['call_id'];current,path=advance(root,'remote_execution:fixture',{})
    target=projection(root,old);value=c.read(target)
    if fault=='missing':target.unlink()
    elif fault=='accepted-attempt':
        target=e.Records(old).directory(call)/'accepted.json';value=c.read(target)
        value['native_attempt']['number']=True;c.write(target,value)
    else:
        if fault=='scope':value['scope']['execution_key']=dict(path='/wrong/original.json')
        elif fault=='record':value['record_dir']+='/other'
        elif fault=='attempt':value['native_attempt']['execution_key']=dict(path='/wrong/original.json')
        elif fault=='interface':value['execution_interface']['config_record']='f'*64
        else:value['outcome']='known_failed'
        c.write(target,value)
    assert cli('query','--call-id',call,config_file=path,expected=3)['status']=='integrity_error'
    assert len(e.Records(current).calls())==1


@pytest.mark.process_e2e
def test_current_and_original_collection_capabilities_both_required(tmp_path,monkeypatch,composed_registry):
    root,job,old,cli=case(tmp_path,monkeypatch,executor='composed_execution:fixture',extra=dict(synchronous=True),resume=True)
    call=run(root,cli,'P')['call_id']
    current,path=advance(root,'composed_execution:fixture',dict(synchronous=True,collect=False))
    assert cli('collect','--call-id',call,config_file=path,expected=2)['status']=='unavailable'
    assert cli('query','--call-id',call,config_file=path)['state']=='succeeded'
