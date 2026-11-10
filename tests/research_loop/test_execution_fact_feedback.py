"""Exact-call feedback through existing CLI/bridge, without a native or solver."""
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from execution_fixtures import (DifferentExternalIdentity, execution_registry,
    remote_registry)
from test_mutable_run_inputs import (fixed_code, live_fragment, live_terminal_code,
    _code, _research)
from scidiscovery.research_loop import (accepted_budget, contracts as c,
    controller, execution_facts, experiments as e, native, worker_execution_bridge)
from scidiscovery.research_loop.local_experiments import LocalExecution


def _server(root, job, record, config):
    work=c.workspace(root,job['step']);path=work/'work/version-config.json'
    def forbidden(*args,**options):pytest.fail('Feedback launched a native or local process')
    return SimpleNamespace(work=work,record=record,config_path=path,
        client=c.read(path),configuration=config,stop=threading.Event(),
        environment={},launch=forbidden)


def _fact(result):
    view=result['execution_facts']
    assert view['scope']['kind']=='single_call' and view['observed_calls']==1
    assert view['coverage']=='accepted_scope' and view['additional_calls']==0
    assert 'scientific validity' in view['interpretation']
    assert 'not independently verified' in result['adapter_report_interpretation']
    return view['calls'][0]


def test_local_bridge_distinguishes_current_query_from_frozen_collection(
        live_fragment,live_terminal_code,monkeypatch):
    root,job,record=live_fragment;row=live_terminal_code()
    config=row['config'];call=row['accepted']['call_id']
    server=_server(root,job,record,config)
    counts=Counter()
    for name in ('status_details','collect_original'):
        original=getattr(LocalExecution,name)
        def counted(self,*args,_name=name,_original=original,**options):
            counts[_name]+=1
            return _original(self,*args,**options)
        monkeypatch.setattr(LocalExecution,name,counted)
    def forbidden(*args,**options):pytest.fail('Active W feedback accepted a stopped inventory')
    monkeypatch.setattr(c,'accept_current_inventory',forbidden)
    before={name:(row['directory']/name).read_bytes() for name in (
        'accepted.json','completed.json','submission.json','terminal.json')}

    reply=worker_execution_bridge._execute(server,['query','--call-id',call])
    assert reply['code']==0 and reply['result']['state']=='succeeded'
    assert 'research_note_hint' not in reply['result']
    fact=_fact(reply['result'])
    assert fact['call_id']==call and fact['status']=='known_success'
    assert fact['observation_basis']=='current_observation'
    assert fact['scientific_result']=='not_accepted'
    assert counts==dict(status_details=1)
    assert not (row['directory']/'execution-observation.json').exists()

    reply=worker_execution_bridge._execute(server,['collect','--call-id',call])
    assert reply['code']==0 and reply['result']['status']=='collected'
    assert 'exp note' in reply['result']['research_note_hint']
    fact=_fact(reply['result'])
    assert fact['observation_basis']=='accepted_terminal'
    # This fixture retains an output file but supplies no optional result.json.
    assert fact['scientific_result']=='missing'
    assert fact['selected_code']['commit']==row['accepted']['code_version']['commit']
    assert fact['native_attempt']==0
    alias=f'work/retained-experiments/{call}/outputs/result.txt'
    assert alias in fact['navigation']['result']
    assert (c.workspace(root,job['step'])/alias).read_bytes()==row['output'].read_bytes()
    assert counts==dict(status_details=2,collect_original=1)
    saved=(row['directory']/'execution-observation.json').read_bytes()

    reply=worker_execution_bridge._execute(server,['query','--call-id',call])
    assert _fact(reply['result'])['observation_basis']=='accepted_terminal'
    assert counts==dict(status_details=3,collect_original=1)
    assert (row['directory']/'execution-observation.json').read_bytes()==saved
    assert all((row['directory']/name).read_bytes()==raw for name,raw in before.items())
    public=json.dumps(reply['result']['execution_facts'])
    assert str(root) not in public and 'host-runtime' not in public


def _remote_owner(tmp_path,*,backend,outcome='known_success'):
    public=tmp_path/'public';public.mkdir()
    goal=public/'goal.md';goal.write_text('Use exact public observations.')
    root=tmp_path/'instance';configuration=dict(outcome=outcome)
    backend_options=(dict(native_backend='pi',native_options=dict(workspace='workspace-write',
        network=True,web='disabled',subagents=False),model='fixture/exact-model',service_tier=None)
        if backend=='pi' else dict(native_backend='protocol',native_options={},model='no-model'))
    controller.prepare(root,goal,public,['never-launched'],max_tokens=1000,
        max_seconds=100,max_steps=8,reasoning_effort='high',search_policy=None,
        experiment_mode='local',execution_config=dict(executor='remote_execution:fixture',
            configuration=configuration),**backend_options)
    job=c.build_job(root,1,'W',[]);record=c.host_record(c.workspace(root,1))
    intent=dict(step=1,task_ref=c.record_ref(root/'jobs/001.json'),
        **native.profiles.binding(c.settings(root),job))
    accepted_budget.accept_native(root,1,record,intent)
    writer=native.runtime.process_identity(os.getpid(),intent['task_ref'])
    for name in ('runner-process.json','native-process.json'):c.write(record/name,writer)
    work=c.workspace(root,1);path=work/'work/version-config.json'
    config=e.configure(root,1,'remote_execution:fixture',configuration,path)
    source=_code(root,job,raw=b'{"message":"remote-original"}')
    research=work/'work/research.json';c.write(research,_research(config))
    return root,job,record,config,source,research


@pytest.mark.parametrize('backend',['protocol','pi'])
def test_remote_metricless_run_query_collect_keep_original_provider_counts(
        tmp_path,remote_registry,fixed_code,monkeypatch,backend):
    root,job,record,config,source,research=_remote_owner(tmp_path,backend=backend)
    server=_server(root,job,record,config);counts=Counter()
    for name in ('prepare','submit','status_details','collect_original'):
        original=getattr(DifferentExternalIdentity,name)
        def counted(self,*args,_name=name,_original=original,**options):
            counts[_name]+=1
            return _original(self,*args,**options)
        monkeypatch.setattr(DifferentExternalIdentity,name,counted)
    def forbidden(*args,**options):pytest.fail('Exact-call feedback scanned a stopped inventory')
    monkeypatch.setattr(c,'accept_current_inventory',forbidden)

    reply=worker_execution_bridge._execute(server,['run','--script',str(source),
        '--research',str(research)])
    assert reply['code']==0 and reply['result']['status']=='known_success'
    assert 'first line' in reply['result']['research_note_hint']
    call=reply['result']['call_id'];fact=_fact(reply['result'])
    assert fact['call_id']==call and fact['scientific_result']=='available'
    assert fact['observation_basis']=='accepted_terminal'
    assert 'metrics' not in reply['result'] and 'result' not in reply['result']
    assert counts==dict(prepare=1,submit=1,status_details=1,collect_original=1)
    directory=e.Records(config).directory(call)
    originals={name:(directory/name).read_bytes() for name in (
        'accepted.json','completed.json','submission.json','execution-observation.json')}

    query=worker_execution_bridge._execute(server,['query','--call-id',call])
    collect=worker_execution_bridge._execute(server,['collect','--call-id',call])
    assert query['code']==collect['code']==0
    assert _fact(query['result'])['observation_basis']=='accepted_terminal'
    assert 'research_note_hint' not in query['result']
    assert 'exp note' in collect['result']['research_note_hint']
    assert _fact(collect['result'])['scientific_result']=='available'
    assert collect['result']['outputs'] and 'metrics' not in collect['result']
    assert counts==dict(prepare=1,submit=1,status_details=2,collect_original=2)
    assert all((directory/name).read_bytes()==raw for name,raw in originals.items())


def test_unknown_remote_run_and_exact_query_never_replay(
        tmp_path,remote_registry,fixed_code,monkeypatch):
    root,job,record,config,source,research=_remote_owner(tmp_path,backend='protocol',outcome='unknown')
    server=_server(root,job,record,config);submissions=[]
    original=DifferentExternalIdentity.submit
    def submit(self,payload):
        submissions.append(payload.name)
        return original(self,payload)
    monkeypatch.setattr(DifferentExternalIdentity,'submit',submit)
    run=worker_execution_bridge._execute(server,['run','--script',str(source),
        '--research',str(research)])
    assert run['code']==2 and run['result']['status']=='unknown'
    call=run['result']['call_id']
    assert _fact(run['result'])['status']=='unknown'
    assert run['result']['execution_facts']['unresolved_calls']==1
    query=worker_execution_bridge._execute(server,['query','--call-id',call])
    assert _fact(query['result'])['status']=='unknown'
    assert _fact(query['result'])['observation_basis']=='current_observation'
    assert submissions==[call]
    assert not (e.Records(config).directory(call)/'execution-observation.json').exists()


def test_projection_preserves_arbitrary_adapter_values_and_performs_no_reads(monkeypatch):
    accepted=dict(call_id='opaque-call',native_attempt=dict(number=2),
        code_version=dict(commit='a'*40),script=dict(path='/private/source'))
    result=dict(status='known_success',call_id='opaque-call',
        metric='domain.metric',result=dict(status='available',metrics=[dict(name='score',value=.75)]),
        domain_report=dict(custom='unchanged'))
    original=deepcopy(result)
    observed=dict(status='known_success',public_result=deepcopy(result),scientific_outputs=[])
    def forbidden(*args,**options):pytest.fail('A feedback consumer acquired new state')
    for name in ('open','stat','resolve'):monkeypatch.setattr(Path,name,forbidden)
    monkeypatch.setattr(e,'load',forbidden)
    value=e._execution_feedback(result,accepted=accepted,observed=observed,command='run',
        operation_started=True,work=None)
    assert all(value[key]==item for key,item in original.items()) and result==original
    assert _fact(value)['selected_code']['commit']=='a'*40
    assert '/private' not in json.dumps(value['execution_facts'])


@pytest.mark.parametrize('state',['accepted','submitted','queued','running','cancelling'])
def test_live_feedback_retains_supported_pending_states(state):
    accepted=dict(call_id='pending-original',native_attempt=dict(number=1))
    response=dict(state=state,call_id='pending-original')
    original=deepcopy(response)
    result=e._execution_feedback(response,accepted=accepted,observed=None,
        command='query',operation_started=True,work=None)
    fact=_fact(result);view=result['execution_facts']
    assert response==original and result['state']==state
    assert fact['call_id']==result['call_id']=='pending-original'
    assert fact['status']==fact['native_state']==state
    assert fact['observation_basis']=='current_observation'
    assert fact['scientific_result']=='not_accepted'
    assert view['unresolved_calls']==1 and view['status_counts'][state]==1
    assert 'research_note_hint' not in result


@pytest.mark.parametrize('status',['known_success','known_failed'])
@pytest.mark.parametrize('command',['run','collect'])
def test_note_hint_accepts_terminal_failure_diagnostics_without_new_reads(monkeypatch,status,command):
    accepted=dict(call_id='exact-original')
    observed=dict(status=status,scientific_outputs=[])
    def forbidden(*args,**options):pytest.fail('Note hint acquired new originals')
    for name in ('open','stat','resolve'):monkeypatch.setattr(Path,name,forbidden)
    result=e._execution_feedback(dict(status=status if command=='run' else 'collected'),
        accepted=accepted,observed=observed,command=command,operation_started=True,work=None)
    assert 'Optionally' in result['research_note_hint']
    assert 'first line' in result['research_note_hint']


@pytest.mark.parametrize('status,observed,started',[
    ('submitted',None,True),('unknown',None,True),('caller_error',None,False),
    ('integrity_error',dict(status='known_failed'),True),
    ('known_success',dict(status='known_success'),False)])
def test_note_hint_skips_unaccepted_or_nonfresh_results(status,observed,started):
    result=e._execution_feedback(dict(status=status),accepted=dict(call_id='exact-original'),
        observed=observed,command='run',operation_started=started,work=None)
    assert 'research_note_hint' not in result


def test_cli_nonascii_fact_view_obeys_its_actual_wire_byte_bound(
        tmp_path,remote_registry,fixed_code,capsys):
    root,job,record,config,source,research=_remote_owner(tmp_path,backend='protocol')
    # Descriptor names remain within their established ASCII contract. The
    # selected code entry supports Unicode without changing that contract.
    selected=source.with_name('研究入口.py');source.rename(selected);source=selected
    path=c.workspace(root,job['step'])/'work/version-config.json'
    errors=[]
    with e.diagnostic_reporter(errors.append,clean=lambda value,limit:value):
        code=e.main(['--config',str(path),'run','--script',str(source),
            '--research',str(research)])
    assert code==0, [repr(error) for error in errors]
    raw=capsys.readouterr().out
    marker='"execution_facts":'
    start=raw.index(marker)+len(marker)
    start+=len(raw[start:])-len(raw[start:].lstrip())
    view,end=json.JSONDecoder().raw_decode(raw[start:])
    wire=raw[start:start+end].encode('utf-8')
    assert '研究入口.py' in wire.decode('utf-8')
    assert len(wire)<=execution_facts.MAX_BYTES
    assert view==json.loads(raw)['execution_facts']
    assert view['calls'][0]['selected_code']['entry']=='研究入口.py'


def test_optional_render_failure_preserves_cli_reply_but_identity_failure_stays_strict(
        live_fragment,live_terminal_code,monkeypatch):
    root,job,record=live_fragment;row=live_terminal_code()
    server=_server(root,job,record,row['config']);call=row['accepted']['call_id']
    def unavailable(**options):raise RuntimeError('/private/provider-display-failure')
    monkeypatch.setattr(execution_facts,'render',unavailable)
    reply=worker_execution_bridge._execute(server,['query','--call-id',call])
    assert reply['code']==0 and reply['result']['state']=='succeeded'
    assert reply['result']['call_id']==call
    assert reply['result']['execution_facts']==execution_facts.unavailable(
        scope=dict(kind='single_call',node='current'))
    assert '/private' not in json.dumps(reply['result'])
    def invalid_original(self,call_id,**options):
        raise e.IntegrityError('Original accepted identity changed')
    monkeypatch.setattr(e.Records,'accepted',invalid_original)
    reply=worker_execution_bridge._execute(server,['query','--call-id',call])
    assert reply['code']==3 and reply['result']['status']=='integrity_error'
    assert 'execution_facts' not in reply['result']
