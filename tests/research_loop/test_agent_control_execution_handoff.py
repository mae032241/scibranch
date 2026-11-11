"""Caller repair and accepted-call identity at the actual execution boundaries.

Trusted local launch and TCAD backend fixtures are injected; no solver/model runs.
"""
import json
import errno
from pathlib import Path

import pytest

from execution_fixtures import execution_registry
from test_local_managed import local
from test_native_execution_inputs import chain
from scidiscovery.research_loop import (contracts as c, controller, execution,
    experiments as e, native_execution, worker_execution_bridge as bridge)
from scidiscovery.research_loop.local_experiments import LocalExecution


@pytest.mark.parametrize('arguments',[
    ['x'*4000]*4+['x'], ['x']*160, ['x'*5000],
],ids=['over-16000-bytes','over-128-items','over-4096-item'])
def test_transportable_argv_reaches_acceptance_without_duplicate_limits(local,monkeypatch,capsys,arguments):
    root,script,config,path=local
    request=['run','--script',str(script),'--',*arguments]
    assert len(bridge._encoded(dict(submission_ref='S001',arguments=request)))<bridge.MAX_REQUEST_BYTES
    assert bridge._arguments(request,root)==request
    assert bridge._request_consumption(request).arguments[1:]==arguments
    submitted=[]
    def submit(self,prepared):
        submitted.append(prepared.name)
        assert self.records.accepted(prepared.name)['command'][-len(arguments):]==arguments
        raise e.UnsupportedExecution('TCAD submission response is unknown; preserve the original call')
    monkeypatch.setattr(LocalExecution,'submit',submit)
    assert e.main(['--config',str(path),*request])==2
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='unknown' and result['accepted'] is True
    assert submitted==[result['call_id']]


def test_local_directory_input_error_is_specific_and_correctable(local,monkeypatch,capsys):
    root,script,config,path=local
    other=root/'other';other.mkdir()
    monkeypatch.setattr(LocalExecution,'submit',lambda self,prepared: (_ for _ in ()).throw(RuntimeError('no backend')))
    command=['--config',str(path),'run','--script',str(script),'--experiment-dir']
    assert e.main([*command,str(other)])==2
    refused=json.loads(capsys.readouterr().out)
    assert refused['status']=='caller_error' and refused['accepted'] is False
    assert 'Script must belong' in refused['note']
    assert refused['handoff']['action']=='correct_request' and not e.Records(config).calls()
    assert e.main([*command,str(root)])==2
    corrected=json.loads(capsys.readouterr().out)
    assert corrected['status']=='unknown' and corrected['accepted'] is True
    assert len(e.Records(config).calls())==1


@pytest.mark.parametrize('failure', ['json','slot','alias','output'])
def test_compile_caller_diagnostic_can_be_corrected_on_same_client(chain,capsys,failure):
    root,backend,_,config,path,author,aliases=chain
    output=c.workspace(root,2)/'artifacts/package.json'
    original=aliases.read_bytes()
    if failure=='json':aliases.write_text('{')
    elif failure=='slot':aliases.write_text('{}')
    elif failure=='alias':aliases.write_text(json.dumps(dict(mesh='inputs/parents/001/artifacts/missing.tdr')))
    else:output.write_bytes(b'preserve existing output')
    command=['--config',str(path),'compile','--implementation',str(author),
        '--scientific-files',str(aliases),'--output',str(output)]
    assert e.main(command)==2
    refused=json.loads(capsys.readouterr().out)
    assert refused['status']=='caller_error' and refused['accepted'] is False,refused
    assert 'call_id' not in refused and not e.Records(config).calls()
    assert not backend.prepared and backend.submits==0
    expected={'json':'line 1, column 2','slot':"missing slots ['mesh']",'alias':"slot 'mesh'",'output':'already exists'}
    assert expected[failure] in refused['note']
    aliases.write_bytes(original)
    if failure=='output':
        assert output.read_bytes()==b'preserve existing output'
        command[-1]=str(output.with_name('corrected-package.json'))
    assert e.main(command)==0
    assert json.loads(capsys.readouterr().out)['status']=='compiled'
    assert not backend.prepared and backend.submits==0


@pytest.mark.parametrize('operation',['submit','result','collect_original'])
def test_postaccept_adapter_error_keeps_exact_id_and_safe_reason(local,monkeypatch,capsys,operation):
    root,script,config,path=local
    def failed(*args,**kwargs):raise OSError(errno.E2BIG,'Argument list too long; token=hidden-credential', str(path.parent/'host-private'))
    monkeypatch.setattr(LocalExecution,'submit',failed if operation=='submit' else lambda self,payload:(payload.name,'running'))
    if operation=='result':monkeypatch.setattr(LocalExecution,'result',failed)
    elif operation=='collect_original':monkeypatch.setattr(LocalExecution,'collect_original',failed)
    command=['--config',str(path),'run','--script',str(script)]
    assert e.main(command)==2
    first=json.loads(capsys.readouterr().out);call=first['call_id']
    if operation=='collect_original':
        assert e.main(['--config',str(path),'collect','--call-id',call])==3
        result=json.loads(capsys.readouterr().out)
    else:result=first
    assert result['status']=='unknown' and result['accepted'] is True and result['call_id']==call
    assert 'OSError' in result['note'] and 'Argument list too long' in result['note']
    assert str(path.parent) not in json.dumps(result) and 'hidden-credential' not in json.dumps(result)


@pytest.mark.parametrize('operation',['submit','query','collect'])
def test_provider_ssh_details_remain_only_in_private_diagnostics(local,monkeypatch,capsys,operation):
    _,script,config,path=local
    private=('ssh failed for hidden-operator@198.51.100.47 via hidden-jump.internal; '
        'IdentityFile=/opt/hidden-ssh/key.pem; opaque-ssh-material-8364')
    def failed(*args,**kwargs):raise OSError(errno.EIO,private)
    monkeypatch.setattr(LocalExecution,'submit',failed if operation=='submit' else lambda self,payload:(payload.name,'running'))
    assert e.main(['--config',str(path),'run','--script',str(script)])==2
    result=json.loads(capsys.readouterr().out);call=result['call_id']
    if operation!='submit':
        monkeypatch.setattr(LocalExecution,'status_details' if operation=='query' else 'collect_original',failed)
        assert e.main(['--config',str(path),operation,'--call-id',call])==3
        result=json.loads(capsys.readouterr().out)
    assert result['status']=='unknown' and result['accepted'] is True and result['call_id']==call
    assert ('submission' if operation=='submit' else operation) in result['note'] and 'OSError (EIO:' in result['note']
    public=json.dumps(result)
    for secret in ('hidden-operator','198.51.100.47','hidden-jump.internal','/opt/hidden-ssh','opaque-ssh-material-8364'):
        assert secret not in public
    diagnostics=Path(config['record_dir']).parent/'execution-diagnostics'
    assert any(private in c.read(item)['note'] for item in diagnostics.iterdir())
    assert len(e.Records(config).calls())==1


@pytest.mark.parametrize('standard',[False,True],ids=['custom-launcher','named-permissions'])
def test_known_unsupported_native_launcher_is_refused_before_local_acceptance(local,monkeypatch,capsys,standard):
    _,script,config,path=local
    root=path.parent/'native-instance';public=path.parent/'native-public';public.mkdir()
    objective=public/'goal.md';objective.write_text('No real execution')
    controller.prepare(root,objective,public,['custom-launcher'],experiment_mode='local',
        max_tokens=1000,max_seconds=30,max_steps=2,model='no-model',reasoning_effort='high',search_policy=None)
    c.build_job(root,1,'W',[]);work=c.workspace(root,1);record=execution.origin(root,1)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/001.json')))
    home=path.parent/'native-home';home.mkdir()
    (home/'config.toml').write_text('default_permissions="named"\n[permissions.named.network]\nenabled=true\n')
    _,launch=native_execution.prepare(root,1,record,work,home,['custom-launcher','exec','-'],standard=standard)
    def forbidden(*args,**kwargs):pytest.fail('Known unsupported native permissions reached prepare/accept/submit')
    monkeypatch.setattr(LocalExecution,'prepare',forbidden)
    monkeypatch.setattr(LocalExecution,'submit',forbidden)
    monkeypatch.setattr(e.Records,'accept',forbidden)
    token=bridge._LOCAL_LAUNCH.set(launch)
    try:assert e.main(['--config',str(path),'run','--script',str(script)])==2
    finally:bridge._LOCAL_LAUNCH.reset(token)
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='known_not_started' and result['accepted'] is False
    assert result['error']=='unsupported_execution' and 'unsupported' in result['note']
    assert result['handoff']['action']=='reconcile_or_stop' and not e.Records(config).calls()


@pytest.mark.parametrize('arguments', [[], ['run',123], ['run','--script','bad\0path']])
def test_basic_argv_shape_and_nul_are_still_rejected(local,arguments):
    root,_,_,_=local
    with pytest.raises(e.CallerError):bridge._arguments(arguments,root)
    with pytest.raises(e.CallerError):bridge._request_consumption(arguments)


def test_real_transport_frame_limit_remains(local):
    raw=bridge._encoded(dict(submission_ref='S001',arguments=['run','--script','run.py','--','x'*bridge.MAX_BYTES]))
    with pytest.raises(ValueError,match='transport bound'):
        bridge._submission(raw)


def test_module_main_preserves_canonical_local_caller_error(local,monkeypatch,capsys):
    import runpy
    import sys
    root,script,config,path=local
    other=root/'wrong-runtime';other.mkdir()
    monkeypatch.setattr(sys,'argv',['exp','--config',str(path),'run','--script',str(script),'--experiment-dir',str(other)])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_module('scidiscovery.research_loop.experiments',run_name='__main__')
    assert stopped.value.code==2
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='caller_error' and result['accepted'] is False
    assert 'Script must belong' in result['note'] and not e.Records(config).calls()


@pytest.mark.parametrize('command', ['run','compile'])
def test_missing_caller_input_is_rejected_before_acceptance(local,capsys,command):
    root,script,config,path=local
    if command=='run':
        arguments=['run','--script',str(root/'missing.py')]
    else:
        arguments=['compile','--implementation',str(script),'--scientific-files',str(root/'missing.json'),
            '--output',str(root/'new-package.json')]
    assert e.main(['--config',str(path),*arguments])==2
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='caller_error' and result['accepted'] is False
    assert result['error']=='scientific_input_not_found' and 'missing' in result['note']
    assert not e.Records(config).calls()


@pytest.mark.parametrize('command',['collect','query'])
def test_original_operation_concurrent_error_after_acceptance_keeps_original_id(local,monkeypatch,capsys,command):
    _,script,config,path=local
    monkeypatch.setattr(LocalExecution,'submit',lambda self,payload:(payload.name,'running'))
    assert e.main(['--config',str(path),'run','--script',str(script)])==2
    call=json.loads(capsys.readouterr().out)['call_id']
    def concurrent(*args,**kwargs):raise e.ConcurrentExecution('original operation lease is busy')
    monkeypatch.setattr(LocalExecution,'collect_original' if command=='collect' else 'status_details',concurrent)
    assert e.main(['--config',str(path),command,'--call-id',call])==2
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='unknown' and result['accepted'] is True and result['call_id']==call
    assert 'ConcurrentExecution' in result['note'] and 'operation response is unknown' in result['note']
    assert len(e.Records(config).calls())==1


@pytest.mark.parametrize('command',['run','compile','capabilities','rnu'])
@pytest.mark.parametrize('frame',['request-limit','transport-limit','response-echo'])
def test_client_oversized_request_gets_host_claim_and_terminal_caller_repair(chain,monkeypatch,capsys,command,frame):
    from types import SimpleNamespace
    root,backend,_,config,config_path,author,aliases=chain
    job=c.read(root/'jobs/002.json');work=c.workspace(root,2);record=c.host_record(work)
    c.write(record/'experiment-config.json',config)
    directory=work/bridge.SUBMISSION_DIRECTORY;directory.mkdir(parents=True,exist_ok=True)
    claims=Path(config['record_dir']).parent/'worker-submissions';claims.mkdir(parents=True,exist_ok=True)
    server=SimpleNamespace(configuration=config,directory=directory,claims=claims,
        client=dict(step=2,attempt=config['native_attempt']['number']),
        diagnostic=Path(config['record_dir']).parent/'frame-diagnostic.json')
    monkeypatch.setattr(bridge,'_configuration',lambda actual:config)
    def forbidden(*args,**kwargs):pytest.fail('Oversized frame must not execute a command or backend')
    monkeypatch.setattr(bridge,'_execute',forbidden)
    publish=bridge._publish
    def synchronously_delivered(path,data):
        publish(path,data)
        if Path(path)==directory/'submit.json':bridge._handle(server,data)
    monkeypatch.setattr(bridge,'_publish',synchronously_delivered)
    if command=='run':arguments=['run','--script',str(author),'--','']
    elif command=='compile':arguments=['compile','--implementation',str(author),'--scientific-files',str(aliases),'--output','']
    else:arguments=[command,'--start','']
    if frame=='request-limit':size=bridge.MAX_REQUEST_BYTES
    elif frame=='transport-limit':size=bridge.MAX_BYTES
    else:size=bridge.MAX_BYTES-len(bridge._encoded(dict(submission_ref='S001',arguments=arguments)))-16
    arguments[-1]='x'*size
    assert bridge.main(['--submission-directory',str(directory),*arguments])==2
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='caller_error' and result['accepted'] is False
    assert result['error']=='submission_frame_too_large'
    assert ('64000 bytes' if frame=='request-limit' else 'rejected before publication') in result['note']
    assert not backend.prepared and backend.submits==0 and not e.Records(config).calls()
    request=bridge._submission(bridge._read_file(claims/'S001/submit.json'))
    outcome=c.read(claims/'S001/outcome.json')
    assert outcome['calls']==[]
    if frame=='request-limit':assert request['arguments']==arguments
    else:
        assert request['client_error']=='submission_frame_too_large' and request['arguments']==[command]
        assert len(bridge._read_file(claims/'S001/submit.json'))<bridge.MAX_REQUEST_BYTES
    repair=bridge.terminal_caller_error(root,job,record)
    if command=='capabilities':assert repair is None
    else:
        assert repair['arguments']==request['arguments']
        assert ('request frame limit' if frame=='request-limit' else 'rejected before publication') in repair['diagnostic']


@pytest.mark.parametrize('failure',['json','required-field'])
def test_compile_implementation_document_error_is_correctable(chain,capsys,failure):
    root,backend,_,config,path,author,aliases=chain
    original=Path(author).read_bytes()
    if failure=='json':Path(author).write_text('{')
    else:
        value=json.loads(original);del value['solver_kind'];Path(author).write_text(json.dumps(value))
    output=c.workspace(root,2)/'artifacts/package.json'
    command=['--config',str(path),'compile','--implementation',str(author),
        '--scientific-files',str(aliases),'--output',str(output)]
    assert e.main(command)==2
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='caller_error' and result['accepted'] is False
    assert 'TCAD implementation JSON is invalid' in result['note']
    assert ('line 1 column 1' if failure=='json' else 'solver_kind: Field required') in result['note']
    assert not e.Records(config).calls() and not backend.prepared and backend.submits==0
    Path(author).write_bytes(original)
    assert e.main(command)==0
    assert json.loads(capsys.readouterr().out)['status']=='compiled'
    assert not backend.prepared and backend.submits==0


@pytest.mark.parametrize('arguments,repair_expected',[
    (['rnu'],True),
    (['capabilities','--unknown-option'],False),
    (['help','not-a-command'],False),
],ids=['unknown-command','metadata-error','help-error'])
def test_host_rejected_unknown_command_returns_original_arguments_for_repair(chain,monkeypatch,arguments,repair_expected):
    from types import SimpleNamespace
    root,backend,_,config,_,_,_=chain
    job=c.read(root/'jobs/002.json');work=c.workspace(root,2);record=c.host_record(work)
    c.write(record/'experiment-config.json',config)
    directory=work/bridge.SUBMISSION_DIRECTORY;directory.mkdir(parents=True,exist_ok=True)
    claims=Path(config['record_dir']).parent/'worker-submissions';claims.mkdir(parents=True,exist_ok=True)
    server=SimpleNamespace(configuration=config,directory=directory,claims=claims,work=work,
        client=dict(step=2,attempt=config['native_attempt']['number']),
        diagnostic=Path(config['record_dir']).parent/'caller-diagnostic.json')
    monkeypatch.setattr(bridge,'_configuration',lambda actual:config)
    publish=bridge._publish
    def synchronously_delivered(path,data):
        publish(path,data)
        if Path(path)==directory/'submit.json':bridge._handle(server,data)
    monkeypatch.setattr(bridge,'_publish',synchronously_delivered)
    response=bridge._client(directory,arguments)
    assert response['code']==2 and response['result']['status']=='caller_error'
    assert response['result']['accepted'] is False
    assert not backend.prepared and backend.submits==0 and not e.Records(config).calls()
    repair=bridge.terminal_caller_error(root,job,record)
    if repair_expected:
        assert repair['arguments']==arguments
        assert repair['diagnostic']==response['result']['note']
        assert 'Choose an existing scientific command' in repair['diagnostic']
    else:assert repair is None
