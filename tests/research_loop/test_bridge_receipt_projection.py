"""Host receipt projection and bounded mechanical correction regressions."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from execution_fixtures import execution_registry
from test_native_execution_inputs import chain, _reviewed_execution
from scidiscovery.research_loop import contracts as c, experiments as e
from scidiscovery.research_loop import worker_delivery, worker_execution_bridge as bridge


def _refusal_claim(claims,config,reference,arguments,result,stamp,*,calls=()):
    claim=claims/reference;claim.mkdir()
    request=dict(submission_ref=reference,arguments=arguments)
    c.write(claim/'submit.json',request)
    c.write(claim/'binding.json',dict(step=2,attempt=0,configuration=config,
        retained_at_start=False,submission=e.proof(claim/'submit.json')))
    response=dict(code=2 if result.get('accepted') is False else 0,result=result,
        submission_ref=reference,request=request)
    c.write(claim/'response.json',response)
    c.write(claim/'outcome.json',dict(submission=e.proof(claim/'binding.json'),
        response=e.proof(claim/'response.json'),calls=list(calls)))
    os.utime(claim/'outcome.json',ns=(stamp,stamp))
    return claim


@pytest.fixture
def refusal_originals(tmp_path,monkeypatch):
    root=tmp_path;record=root/'attempt';record.mkdir()
    config=dict(native_attempt=dict(number=0,execution_key='original'),
        scope=dict(step=2),record_dir=str(root/'experiments'/'records'))
    c.write(record/'experiment-config.json',config)
    claims=root/'experiments'/'worker-submissions';claims.mkdir(parents=True)
    calls=root/'calls';calls.mkdir()
    monkeypatch.setattr(bridge,'_verify_calls',lambda *args,**kwargs:True)
    monkeypatch.setattr('scidiscovery.research_loop.execution.record',lambda *args:record)
    monkeypatch.setattr(c,'settings',lambda *args:{})
    monkeypatch.setattr('scidiscovery.research_loop.public_output.cleaner',lambda *args:lambda text,limit:text[:limit])
    monkeypatch.setattr(e,'Records',lambda config:SimpleNamespace(
        calls=lambda:list(calls.iterdir()),
        accepted=lambda reference:dict(native_attempt=config['native_attempt'])))
    monkeypatch.setattr(bridge,'_call_evidence',lambda config,reference:dict(
        call_id=reference,observation={'original':'verified'},retirement=None))
    return root,record,config,claims,calls


def test_older_mtime_tie_does_not_hide_later_typed_refusal(refusal_originals,monkeypatch):
    root,record,config,claims,_=refusal_originals
    refusal=dict(status='caller_error',accepted=False,note='Check required options.')
    ordered=[_refusal_claim(claims,config,f'S{number:03d}',['run'],refusal,stamp)
        for number,stamp in ((1,100),(2,100),(3,200))]
    original=Path.iterdir
    monkeypatch.setattr(Path,'iterdir',lambda path:iter(ordered) if path==claims else original(path))
    assert bridge.terminal_caller_error(root,dict(step=2),record)==dict(
        diagnostic='Check required options.',arguments=['run'])


def test_actual_latest_mtime_tie_does_not_choose_request(refusal_originals):
    root,record,config,claims,_=refusal_originals
    refusal=dict(status='caller_error',accepted=False,note='Check required options.')
    for number in (1,2):_refusal_claim(claims,config,f'S{number:03d}',['run'],refusal,200)
    assert bridge.terminal_caller_error(root,dict(step=2),record) is None


@pytest.mark.parametrize('late,unknown',[(True,False),(False,True)])
def test_later_or_unresolved_accepted_call_suppresses_correction(refusal_originals,monkeypatch,late,unknown):
    root,record,config,claims,calls=refusal_originals
    _refusal_claim(claims,config,'S001',['run'],
        dict(status='caller_error',accepted=False,note='Check required options.'),200)
    call=calls/'original-call';call.mkdir();c.write(call/'accepted.json',{})
    stamp=300 if late else 100
    os.utime(call/'accepted.json',ns=(stamp,stamp))
    if unknown:
        monkeypatch.setattr(bridge,'_call_evidence',lambda config,reference:dict(
            call_id=reference,observation=None,retirement=None))
    assert bridge.terminal_caller_error(root,dict(step=2),record) is None


def test_prior_host_claim_binds_acceptance_before_refusal_despite_file_mtime(refusal_originals):
    root,record,config,claims,calls=refusal_originals
    _refusal_claim(claims,config,'S001',['run'],dict(status='known_success',accepted=True),100,
        calls=[dict(call_id='original-call')])
    _refusal_claim(claims,config,'S002',['run'],
        dict(status='caller_error',accepted=False,note='Check required options.'),200)
    call=calls/'original-call';call.mkdir();c.write(call/'accepted.json',{})
    os.utime(call/'accepted.json',ns=(300,300))
    assert bridge.terminal_caller_error(root,dict(step=2),record)['arguments']==['run']


@pytest.mark.parametrize('search',[None,dict(focus=dict(action='improve',mechanism_ref='h001.m1'))])
def test_non_screen_without_typed_refusal_does_not_capture_facts(tmp_path,monkeypatch,search):
    job=dict(role='W',search=search)
    monkeypatch.setattr(worker_delivery,'_caller_correction',lambda *args:None)
    monkeypatch.setattr(worker_delivery,'validate',lambda *args,**kwargs:pytest.fail('Unneeded fact validation'))
    assert worker_delivery.capture(tmp_path,job,tmp_path) is None
    assert worker_delivery.assessment(tmp_path,job,dict(stage_intent='decision'),tmp_path)['status']=='not_applicable'
    assert not (tmp_path/worker_delivery.FACT_NAME).exists()


def test_non_screen_assessment_reuses_its_verified_refusal_for_lazy_capture(tmp_path,monkeypatch):
    job=dict(role='W')
    correction=dict(mechanism_ref='unaccepted run request',diagnostic='Check required options.')
    scanned=[];captured=[]
    def refusal(*args):
        scanned.append(args)
        return correction
    def capture(root,job,record,*,correction=None):
        captured.append(correction)
        c.write(record/worker_delivery.FACT_NAME,{})
    monkeypatch.setattr(worker_delivery,'_caller_correction',refusal)
    monkeypatch.setattr(worker_delivery,'capture',capture)
    monkeypatch.setattr(worker_delivery,'validate',lambda *args,**kwargs:dict(
        evidence=[],unknown=[],prior_unknown=False,capacity=False))
    result=worker_delivery.assessment(tmp_path,job,{},tmp_path)
    assert result['status']=='incomplete' and result['caller_correction']==correction
    assert len(scanned)==1 and captured==[correction]


def test_real_transport_failure_gets_local_reconciliation_hint(tmp_path,capsys):
    missing=tmp_path/'missing-submission-directory'
    assert bridge.main(['--submission-directory',str(missing),'query','--call-id','original-call'])==3
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='unknown'
    assert result['handoff']['action']=='reconcile_or_stop'
    assert result['handoff']['reason']=='original_configuration_unavailable'


def test_host_verified_terminal_hint_survives_actual_client_and_cli_stdout(chain,monkeypatch,capsys):
    root,backend,output,path,config,_=_reviewed_execution(chain,capsys)
    assert e.main(['--config',str(path),'run','--payload',str(output)])==2
    call=json.loads(capsys.readouterr().out)['call_id']
    backend.state='succeeded'
    assert e.main(['--config',str(path),'collect','--call-id',call])==0
    capsys.readouterr()
    observed=e.Records(config).directory(call)/'execution-observation.json'
    assert c.read(observed)['status']=='known_success'
    work=c.workspace(root,config['scope']['step'])
    directory=work/bridge.SUBMISSION_DIRECTORY;directory.mkdir(parents=True,exist_ok=True)
    claims=Path(config['record_dir']).parent/'worker-submissions';claims.mkdir(parents=True,exist_ok=True)
    server=SimpleNamespace(directory=directory,claims=claims,
        configuration=config,client=dict(step=config['scope']['step'],attempt=config['native_attempt']['number']),
        diagnostic=Path(config['record_dir']).parent/'bridge-diagnostic.json')
    monkeypatch.setattr(bridge,'_configuration',lambda server:config)
    executions=[]
    def execute(server,arguments):
        executions.append(arguments)
        # The operation response remains uncertain; verified retained terminal
        # originals still own the public consumption hint for this exact call.
        return dict(code=2,result=dict(status='unknown',accepted=True,call_id=call))
    monkeypatch.setattr(bridge,'_execute',execute)
    publish=bridge._publish
    def host_dispatch(path,raw):
        publish(path,raw)
        if Path(path)==directory/'submit.json':bridge._handle(server,raw)
    monkeypatch.setattr(bridge,'_publish',host_dispatch)
    assert bridge.main(['--submission-directory',str(directory),'collect','--call-id',call])==2
    result=json.loads(capsys.readouterr().out)
    assert result['handoff']['action']=='consume_terminal'
    assert result['handoff']['reason']=='original_terminal_verified'
    assert result['handoff']['call_id']==call
    assert c.read(claims/'S001'/'response.json')['result']['call_id']==call
    originals={name:(claims/'S001'/name).read_bytes()
        for name in ('submit.json','binding.json','response.json','outcome.json')}
    bridge._handle(server,(directory/'submit.json').read_bytes())
    assert c.read(directory/'response.json')['result']['handoff']==result['handoff']
    assert len(executions)==1
    assert backend.submits==1 and len(e.Records(config).calls())==1
    assert all((claims/'S001'/name).read_bytes()==raw for name,raw in originals.items())
