"""Historical call IDs cannot become current-task execution receipt proofs."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import contracts as c, experiments as e
from scidiscovery.research_loop import worker_execution_bridge as bridge


CALL_COMMANDS=('query','lookup','collect','cancel','reconcile','retire')


@pytest.fixture
def server(tmp_path,monkeypatch):
    config=dict(record_dir=str(tmp_path/'current-records'),
        scope=dict(step=6,role='W'),native_attempt=dict(number=0,execution_key='current'))
    directory=tmp_path/'work/experiment';directory.mkdir(parents=True)
    claims=tmp_path/'claims';claims.mkdir()
    server=SimpleNamespace(directory=directory,claims=claims,configuration=config,
        client=dict(step=6,attempt=0),diagnostic=tmp_path/'bridge-diagnostic.json')
    monkeypatch.setattr(bridge,'_configuration',lambda server:config)
    return server


def _request(arguments,reference='S001'):
    return bridge._encoded(dict(submission_ref=reference,arguments=arguments))


@pytest.mark.parametrize('command,historical,inline',[
    ('query',False,False),('lookup',True,True),('collect',True,False),
    ('cancel',False,True),('reconcile',False,False),('retire',True,True),
])
def test_call_outside_current_task_is_cached_caller_error(server,tmp_path,monkeypatch,historical,command,inline):
    old='old-W002-call'
    historical_files={}
    if historical:
        directory=tmp_path/'older-records/calls'/old;directory.mkdir(parents=True)
        c.write(directory/'accepted.json',dict(call_id=old,scope=dict(step=2,role='W')))
        c.write(directory/'execution-observation.json',dict(call_id=old,status='known_success'))
        output=directory/'outer_predictions.npz';output.write_bytes(b'exact historical output')
        historical_files={path:path.read_bytes() for path in (
            directory/'accepted.json',directory/'execution-observation.json',output)}
    executions=[]
    def execute(*args,**kwargs):
        executions.append(args)
        pytest.fail('An out-of-task call operation must not invoke execution or a provider')
    monkeypatch.setattr(bridge,'_execute',execute)
    parses=[];parse=bridge._request_consumption
    def parse_request(arguments):
        parses.append(arguments)
        return parse(arguments)
    monkeypatch.setattr(bridge,'_request_consumption',parse_request)
    raw=_request([command,*([f'--call-id={old}'] if inline else ['--call-id',old])])

    bridge._handle(server,raw)

    response=c.read(server.directory/'response.json');result=response['result']
    assert response['code']==2 and result['status']=='caller_error'
    assert result['accepted'] is False and result['error']=='call_outside_current_task'
    assert 'current W task' in result['note'] and 'inputs/history node index' in result['note']
    assert 'material IDs' in result['note'] and 'Do not resubmit' in result['note']
    assert 'operation request only' in result['note'] and 'original owner' in result['note']
    assert result['handoff']['action']=='correct_request'
    assert result['handoff']['reason']=='execution_not_accepted'
    assert 'state' not in result and 'call_id' not in result
    assert str(tmp_path) not in json.dumps(result)
    assert c.read(server.claims/'S001/outcome.json')['calls']==[]
    assert server.diagnostic.exists()
    originals={name:(server.claims/'S001'/name).read_bytes()
        for name in ('submit.json','binding.json','response.json','outcome.json')}
    published=(server.directory/'response.json').read_bytes()

    bridge._handle(server,raw)

    assert (server.directory/'response.json').read_bytes()==published
    assert all((server.claims/'S001'/name).read_bytes()==value for name,value in originals.items())
    assert len(parses)==1 and executions==[]
    assert all(path.read_bytes()==value for path,value in historical_files.items())
    assert e.Records(server.configuration).calls()==[]


@pytest.mark.parametrize('command',CALL_COMMANDS)
def test_request_id_without_current_original_does_not_create_a_proof(server,command):
    request=dict(arguments=[command,'--call-id','old-W002-call'])
    response=dict(code=2,result=dict(status='caller_error',note='Use the current task.'))
    assert bridge._response_calls(server,request,response,set())==[]


@pytest.mark.parametrize('options',[
    [],['--call-id'],['--call-id','old-W002-call','--metadata'],
    ['--call-id','old-W002-call','--','script-tail'],['--call-id',None],
])
def test_invalid_call_arguments_are_parser_caller_errors(server,monkeypatch,options):
    # All six commands register the same required --call-id parser option.
    command='collect'
    monkeypatch.setattr(bridge,'_execute',lambda *args:pytest.fail('Invalid call arguments cannot execute'))

    bridge._handle(server,_request([command,*options]))

    response=c.read(server.directory/'response.json');result=response['result']
    assert response['code']==2 and result['status']=='caller_error'
    assert result['accepted'] is False and result['error']=='invalid_arguments'
    assert result['handoff']['action']=='correct_request'
    assert c.read(server.claims/'S001/outcome.json')['calls']==[]
    assert server.diagnostic.exists()
    assert str(server.diagnostic.parent) not in json.dumps(result)


@pytest.mark.parametrize('command',CALL_COMMANDS)
def test_scope_uses_the_parser_selected_call_id(server,monkeypatch,command):
    call_id='current-accepted-call';_accepted(server,call_id)
    monkeypatch.setattr(bridge,'_execute',lambda *args:pytest.fail('The final selected call is outside this task'))

    bridge._handle(server,_request([command,'--call-id',call_id,'--call-id=old-W002-call']))

    result=c.read(server.directory/'response.json')['result']
    assert result['status']=='caller_error' and result['error']=='call_outside_current_task'
    assert result['handoff']['action']=='correct_request'
    assert c.read(server.claims/'S001/outcome.json')['calls']==[]


def _accepted(server,call_id):
    directory=Path(server.configuration['record_dir'])/'calls'/call_id
    directory.mkdir(parents=True)
    script=directory/'script.py';script.write_text('print("original")\n')
    accepted=dict(call_id=call_id,scope=server.configuration['scope'],
        parent=dict(candidate_id=None),interface={},script=e.proof(script),snapshot_files=[])
    c.write(directory/'accepted.json',accepted)
    return directory,accepted


@pytest.mark.parametrize('command',CALL_COMMANDS)
def test_current_corrupt_original_is_not_a_scope_caller_error(server,monkeypatch,command):
    call_id='current-accepted-call';directory,_=_accepted(server,call_id)
    (directory/'accepted.json').write_bytes(b'{')
    executions=[]
    def execute(server,arguments,**kwargs):
        executions.append(arguments)
        e.Records(server.configuration).accepted(call_id)
        pytest.fail('Corrupt originals cannot reach an execution provider')
    monkeypatch.setattr(bridge,'_execute',execute)

    bridge._handle(server,_request([command,f'--call-id={call_id}']))

    response=c.read(server.directory/'response.json');result=response['result']
    assert response['code']==3 and result['status']=='integrity_error'
    assert result.get('error')!='call_outside_current_task' and result.get('accepted') is not False
    assert executions==[[command,f'--call-id={call_id}']]
    assert (directory/'accepted.json').read_bytes()==b'{'


@pytest.mark.parametrize('command',CALL_COMMANDS)
def test_changed_current_accepted_original_remains_integrity_error(server,monkeypatch,command):
    call_id='current-accepted-call';directory,accepted=_accepted(server,call_id)
    raw=_request([command,'--call-id',call_id]);request=json.loads(raw)
    claim=server.claims/'S001';claim.mkdir()
    bridge._publish(claim/'submit.json',raw)
    c.write(claim/'binding.json',dict(step=6,attempt=0,configuration=server.configuration,
        retained_at_start=False,submission=e.proof(claim/'submit.json')))
    c.write(claim/'response.json',dict(code=2,result=dict(status='unknown',accepted=True,call_id=call_id),
        submission_ref='S001',request=request))
    c.write(claim/'outcome.json',dict(submission=e.proof(claim/'binding.json'),
        response=e.proof(claim/'response.json'),calls=[bridge._call_evidence(server.configuration,call_id)]))
    originals={name:(claim/name).read_bytes() for name in ('submit.json','binding.json','response.json','outcome.json')}
    c.write(directory/'accepted.json',dict(accepted,scope=dict(step=2,role='W')))
    monkeypatch.setattr(bridge,'_execute',lambda *args:pytest.fail('A retained claim must not execute again'))

    bridge._handle(server,raw)

    response=c.read(server.directory/'response.json')
    assert response['code']==3 and response['result']['status']=='integrity_error'
    assert all((claim/name).read_bytes()==value for name,value in originals.items())


@pytest.mark.parametrize('command',CALL_COMMANDS)
def test_current_accepted_unknown_stays_unknown_and_is_not_reexecuted(server,monkeypatch,command):
    call_id='current-accepted-call';_accepted(server,call_id)
    # This fixture has no scientific output/control split; the real receipt and
    # original-byte validators still own acceptance and script integrity.
    monkeypatch.setattr(e,'_observation_file_roles',lambda *args,**kwargs:([],set()))
    executions=[]
    def execute(server,arguments,**kwargs):
        executions.append(arguments)
        return dict(code=2,result=dict(status='unknown',accepted=True,call_id=call_id))
    monkeypatch.setattr(bridge,'_execute',execute)
    raw=_request([command,'--call-id',call_id])

    bridge._handle(server,raw)
    assert c.read(server.directory/'response.json')['result']['status']=='unknown'
    bridge._handle(server,raw)

    result=c.read(server.directory/'response.json')['result']
    assert result['status']=='unknown' and result['call_id']==call_id
    assert len(executions)==1


@pytest.mark.parametrize('command',CALL_COMMANDS)
def test_retained_unfinished_call_claim_stays_unknown(server,monkeypatch,command):
    monkeypatch.setattr(bridge,'_execute',lambda *args:pytest.fail('Retained requests cannot execute'))
    raw=_request([command,'--call-id','old-W002-call'])

    bridge._handle(server,raw,retained=True)

    result=c.read(server.directory/'response.json')['result']
    assert result['status']=='unknown'
    assert c.read(server.claims/'S001/outcome.json')['calls']==[]


@pytest.fixture
def unknown_preparation(server,tmp_path,monkeypatch):
    call_id='unresolved-preparation-call';executions=[];preparations=[]
    def execute(server,arguments,**kwargs):
        executions.append(arguments)
        assert arguments[0]=='run'
        return dict(code=2,result=dict(status='unknown',call_id=call_id))
    def retained(root,records,*,context):
        preparations.append((root,records))
        return [dict(call_id=call_id,resolved=False)]
    monkeypatch.setattr(bridge,'_execute',execute)
    monkeypatch.setattr('scidiscovery.research_loop.experiment_client.root_for_configuration',
        lambda config,**kwargs:tmp_path)
    monkeypatch.setattr('scidiscovery.research_loop.preparation_recovery.retained_preparations',retained)
    raw=_request(['run','--script','work/script.py'])
    return SimpleNamespace(call_id=call_id,raw=raw,executions=executions,preparations=preparations)


def test_unaccepted_unknown_preparation_retains_original_call_and_does_not_reexecute(server,unknown_preparation):
    scene=unknown_preparation

    bridge._handle(server,scene.raw)
    first=c.read(server.directory/'response.json')['result']
    bridge._handle(server,scene.raw)

    result=c.read(server.directory/'response.json')['result']
    assert first['status']==result['status']=='unknown'
    assert first['call_id']==result['call_id']==scene.call_id
    assert c.read(server.claims/'S001/outcome.json')['calls']==[]
    assert len(scene.executions)==len(scene.preparations)==1


def test_new_query_cannot_reclassify_an_unknown_preparation(server,unknown_preparation):
    scene=unknown_preparation
    bridge._handle(server,scene.raw)
    claim=server.claims/'S001'
    originals={name:(claim/name).read_bytes()
        for name in ('submit.json','binding.json','response.json','outcome.json')}

    bridge._handle(server,_request(['query',f'--call-id={scene.call_id}'],reference='S002'))

    result=c.read(server.directory/'response.json')['result']
    assert result['status']=='caller_error' and result['accepted'] is False
    assert result['error']=='call_outside_current_task' and 'state' not in result
    assert 'unknown submission' in result['note'] and 'original owner' in result['note']
    assert all((claim/name).read_bytes()==value for name,value in originals.items())
    preserved=c.read(claim/'response.json')['result']
    assert preserved['status']=='unknown' and preserved['call_id']==scene.call_id
    assert c.read(server.claims/'S002/outcome.json')['calls']==[]
    assert len(scene.executions)==len(scene.preparations)==1

    bridge._handle(server,scene.raw)
    restored=c.read(server.directory/'response.json')['result']
    assert restored['status']=='unknown' and restored['call_id']==scene.call_id
    assert len(scene.executions)==len(scene.preparations)==1


def test_refused_collect_preserves_existing_historical_implementation_read(server,tmp_path,monkeypatch):
    from scidiscovery.research_loop import accepted_budget, accepted_prefix, handoff_state, history, originals
    old='old-W002-call';older=tmp_path/'older-node/artifacts';older.mkdir(parents=True)
    materials=[]
    for number in range(7):
        path=older/f'{number:03d}.txt';path.write_text(f'original material {number}\n')
        materials.append(dict(path=f'inputs/parents/002/artifacts/{path.name}',source=str(path),
            **{key:value for key,value in e.proof(path).items() if key!='path'}))
    output=older/'outer_predictions.npz';output.write_bytes(b'exact historical array original')
    materials.append(dict(path=f'inputs/parents/002/artifacts/experiments/{old}/artifacts/{output.name}',
        source=str(output),**{key:value for key,value in e.proof(output).items() if key!='path'}))
    seal=dict(materials=materials);receipt=dict(seal_ref='accepted-W002-seal')
    owner=originals.NodeSource(origin_identity={},step=2,original_root=str(tmp_path),storage_root=tmp_path)
    from types import SimpleNamespace
    head=SimpleNamespace(base=dict(frozen='accepted settings'))
    facts=dict(owner=owner);loads=[];head_reads=[]
    def load_head(root):
        assert root==tmp_path
        head_reads.append(root)
        return head
    def member(root,receipts,step):
        assert root==tmp_path and receipts==['frozen-prefix'] and step==2
        return receipt
    def source_facts(root,step,*,head,base):
        assert root==tmp_path and step==2 and base is head.base
        loads.append(step)
        return facts
    monkeypatch.setattr(accepted_budget,'read',load_head)
    monkeypatch.setattr(accepted_prefix,'member',member)
    monkeypatch.setattr(handoff_state,'source_facts',source_facts)
    monkeypatch.setattr(handoff_state,'source_from_facts',
        lambda selected:(dict(role='W',step=2),None,seal,receipt['seal_ref']))
    monkeypatch.setattr(bridge,'_execute',lambda *args:pytest.fail('Historical reading cannot execute a call'))

    bridge._handle(server,_request(['collect','--call-id',old]))
    assert c.read(server.directory/'response.json')['result']['status']=='caller_error'
    material=history.resolve_implementation(tmp_path,'W002-I008',['frozen-prefix'])

    assert material['path']==materials[-1]['path'] and Path(material['source'])==output
    assert Path(material['source']).read_bytes()==b'exact historical array original'
    assert head_reads==[tmp_path] and loads==[2] and e.Records(server.configuration).calls()==[]
