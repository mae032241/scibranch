"""Current-operation refusals retain verified original execution facts."""
import json

import pytest

from execution_fixtures import execution_registry
from test_local_managed import local
from scidiscovery.research_loop import experiments as e
from scidiscovery.research_loop.local_experiments import LocalExecution


@pytest.mark.process_e2e
@pytest.mark.parametrize('command',['query','collect','cancel'])
@pytest.mark.parametrize('damage',[False,True],ids=['verified-terminal','damaged-original'])
def test_refused_operation_keeps_verified_terminal_without_provider_calls(local,monkeypatch,capsys,command,damage):
    root,script,config,path=local
    assert e.main(['--config',str(path),'run','--script',str(script),'--experiment-dir',str(root)])==0
    completed=json.loads(capsys.readouterr().out)
    call=completed['call_id'];directory=e.Records(config).directory(call)
    originals={name:(directory/name).read_bytes() for name in (
        'accepted.json','submission.json','terminal.json','completed.json','execution-observation.json')}
    if damage:
        observation=directory/'execution-observation.json'
        observation.chmod(0o600);observation.write_bytes(b'{')
    def forbidden(*args,**kwargs):pytest.fail('A refused operation must only read retained originals')
    for name in ('prepare','submit','lookup_submission','status_details','result','collect_original','cancel_original'):
        monkeypatch.setattr(LocalExecution,name,forbidden)
    if command!='cancel':
        # An explicit core original-interface preflight denial. Local cancel is
        # naturally absent from its frozen lifecycle contract.
        def unavailable(*args,**kwargs):raise e.UnsupportedExecution('Original interface capability unavailable')
        monkeypatch.setattr(e,'original_adapter',unavailable)
    assert e.main(['--config',str(path),command,'--call-id',call])==(3 if damage else 2)
    result=json.loads(capsys.readouterr().out)
    assert result['accepted'] is True and result['call_id']==call
    if damage:
        assert result['status']=='integrity_error' and 'state' not in result
        assert result['handoff']['action']=='reconcile_or_stop'
        assert 'integrity' in result['note']
    else:
        assert result['status']=='unavailable' and result['state']=='known_success'
        assert result['handoff']['action']=='consume_terminal'
        assert result['handoff']['call_id']==call and 'was not performed' in result['note']
    assert len(e.Records(config).calls())==1
    for name,data in originals.items():
        if name!='execution-observation.json' or not damage:assert (directory/name).read_bytes()==data


def test_cancel_capability_refusal_without_terminal_does_not_claim_original_unknown(local,monkeypatch,capsys):
    _,script,config,path=local
    monkeypatch.setattr(LocalExecution,'submit',lambda self,payload:(payload.name,'running'))
    assert e.main(['--config',str(path),'run','--script',str(script)])==2
    call=json.loads(capsys.readouterr().out)['call_id']
    def forbidden(*args,**kwargs):pytest.fail('Capability refusal cannot query, collect or submit')
    for name in ('submit','status_details','result','collect_original','cancel_original'):
        monkeypatch.setattr(LocalExecution,name,forbidden)
    assert e.main(['--config',str(path),'cancel','--call-id',call])==2
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='unavailable' and result['accepted'] is True and result['call_id']==call
    assert 'state' not in result and result['handoff']['reason']=='original_operation_unavailable'
    assert len(e.Records(config).calls())==1


@pytest.mark.process_e2e
@pytest.mark.parametrize('command',['query','collect'])
@pytest.mark.parametrize('terminal',['known_success','known_failed'])
def test_provider_operation_failure_retains_existing_terminal_state(local,monkeypatch,capsys,command,terminal):
    root,script,config,path=local
    if terminal=='known_failed':
        with script.open('a') as stream:stream.write('sys.exit(7)\n')
    assert e.main(['--config',str(path),'run','--script',str(script),'--experiment-dir',str(root)])==(0 if terminal=='known_success' else 1)
    completed=json.loads(capsys.readouterr().out)
    call=completed['call_id'];directory=e.Records(config).directory(call)
    originals={name:(directory/name).read_bytes() for name in (
        'accepted.json','submission.json','process.json','terminal.json','completed.json',
        'execution-observation.json','stdout.raw','stderr.raw','outputs/value.txt','outputs/result.json')}
    called=[]
    def failed(*args,**kwargs):
        called.append(command)
        raise RuntimeError('synthetic private SSH destination hidden-node.internal')
    def forbidden(*args,**kwargs):pytest.fail('Failure feedback cannot query, collect, rebuild or submit')
    for name in ('prepare','submit','lookup_submission','status_details','result','collect_original','cancel_original'):
        monkeypatch.setattr(LocalExecution,name,forbidden)
    monkeypatch.setattr(LocalExecution,'status_details' if command=='query' else 'collect_original',failed)
    assert e.main(['--config',str(path),command,'--call-id',call])==3
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='unknown' and result['accepted'] is True
    assert result['call_id']==call and result['state']==terminal
    assert result['handoff']['action']=='consume_terminal' and result['handoff']['call_id']==call
    assert 'hidden-node.internal' not in json.dumps(result)
    assert called==[command] and len(e.Records(config).calls())==1
    assert all((directory/name).read_bytes()==data for name,data in originals.items())


def test_accepted_input_integrity_error_never_becomes_caller_repair(local,monkeypatch,capsys):
    _,script,config,path=local
    def damaged(*args,**kwargs):raise e.IntegrityError('private original path /opt/hidden-call/control.json')
    def forbidden(*args,**kwargs):pytest.fail('Damaged accepted inputs cannot reach submit')
    monkeypatch.setattr(e,'verify_scientific_inputs',damaged)
    monkeypatch.setattr(LocalExecution,'submit',forbidden)
    assert e.main(['--config',str(path),'run','--script',str(script)])==3
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='integrity_error' and result['accepted'] is True
    assert result['handoff']['action']=='reconcile_or_stop'
    assert '/opt/hidden-call' not in json.dumps(result)
    assert e.Records(config).accepted(result['call_id'])['call_id']==result['call_id']
