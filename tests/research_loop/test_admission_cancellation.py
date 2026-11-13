"""Live local preparation refusal and original execution uncertainty stay distinct."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from execution_fixtures import execution_registry, remote_registry, DifferentExternalIdentity
from test_formal_review_admission import publish
from test_history_handoff import finish
from test_nonlocal_execution_observation import case
from test_execution_resources import prepare as resource_instance, resource_registry
from scidiscovery.research_loop import accepted_budget, breadth, budget, contracts as c, execution, experiments as e, search
from scidiscovery.research_loop import execution_resume, preparation_recovery
from scidiscovery.research_loop.local_experiments import LocalExecution


def _source(root, name='local.py'):
    # New W code entries belong to the declared code/ scope.
    path=c.workspace(root,2)/'code'/name
    path.parent.mkdir(exist_ok=True)
    path.write_text('print("bounded local fixture")\n')
    return path


# A non-budget host review request; reserve reasons report budget refusals instead.
REVIEW='independent_review_requested'


def _research(config, label='P'):
    """One explicit current-W declaration; it grants no scientific qualification."""
    mechanism=f'w{config["scope"]["step"]:03d}.{label.lower()}'
    return dict(declarations=[dict(mechanism_ref=mechanism,action='declare',
        question=f'Can fixture mechanism {label} support exact collection?')],
        mechanism_ref=mechanism,experiment_question=dict(
            question=f'Does fixture mechanism {label} preserve the declared original entity?',
            change_criterion='A changed entity or private alias must be rejected.'))


def _research_file(root, config, label='P'):
    path=c.workspace(root,2)/'work'/f'research-{label.lower()}.json'
    c.write(path,_research(config,label))
    return str(path)


def _cli(root, capsys, *arguments, expected=2):
    config=c.workspace(root,2)/'work/experiment-config.json'
    assert e.main(['--config',str(config),*map(str,arguments)])==expected
    captured=capsys.readouterr()
    assert not captured.err
    return json.loads(captured.out)


def _temporary(records):
    return [path for path in records.root.iterdir() if path.name.startswith('.prepared-')]


def _no_host_preparation(root, config):
    records=e.Records(config)
    assert preparation_recovery.retained_preparations(root,records.root)==[]
    assert not (records.root/'host-preparations').exists()


def _runtime_owner_fault(monkeypatch, source):
    """Install a controlled fault inside the actual freshly exec'd runner."""
    from scidiscovery.research_loop import runtime
    popen=runtime.subprocess.Popen
    invocation='runpy.run_module("scidiscovery.research_loop.runtime",run_name="__main__")'
    def spawn(*args,**kwargs):
        arguments=args[0] if args else kwargs.get('args')
        if (isinstance(arguments,(list,tuple)) and len(arguments)==8
                and arguments[:2]==[sys.executable,'-c'] and arguments[4]=='runner'
                and arguments[6]=='2' and invocation in arguments[2]):
            arguments=list(arguments)
            arguments[2]=arguments[2].replace(invocation,'exec('+repr(source)+');'+invocation,1)
            if args:args=(arguments,*args[1:])
            else:kwargs=dict(kwargs,args=arguments)
        return popen(*args,**kwargs)
    monkeypatch.setattr(runtime.subprocess,'Popen',spawn)


@pytest.mark.parametrize('mechanism',[None,'not-authorized'])
def test_real_instance_mechanism_refusal_precedes_local_prepare(tmp_path, monkeypatch, execution_registry, capsys, mechanism):
    root,_,config,_=case(tmp_path,monkeypatch,executor='local_execution:command')
    source=_source(root)
    originals={path:path.read_bytes() for path in (root/'settings.json',root/'jobs/002.json',
        execution.record(root,2)/'native-intent.json',execution.record(root,2)/'experiment-config.json')}
    def forbidden(*args,**kwargs):pytest.fail('Known admission refusal reached preparation or metadata')
    monkeypatch.setattr(LocalExecution,'prepare',forbidden)
    monkeypatch.setattr(LocalExecution,'acceptance_metadata',forbidden)
    arguments=['run','--script',source]
    if mechanism is not None:arguments+=['--mechanism',mechanism]
    result=_cli(root,capsys,*arguments)
    assert result['status']=='unresolved' and result['accepted'] is False and 'call_id' not in result
    assert result['handoff']['reason']=='execution_not_accepted' and 'call_id' not in result['handoff']
    assert e.Records(config).calls()==[] and _temporary(e.Records(config))==[]
    _no_host_preparation(root,config)
    assert execution_resume.capture_pending(root,c.read(root/'jobs/002.json')) is None
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    assert config.get('host_execution_authority') is None
    assert not (root/'execution-authority/calls').exists()


def test_real_instance_review_refusal_precedes_authority_allocation(tmp_path, monkeypatch, resource_registry, capsys):
    root=resource_instance(tmp_path)
    finish(root,1,'W',[])
    job=c.build_job(root,2,'W',[1]);record=execution.record(root,2)
    node_anchor=budget.accept_node(root,job)
    anchor=dict(budget_slice=node_anchor) if node_anchor is not None else dict(search_slice=search.accept(root,job))
    accepted_budget.accept_native(root,2,record,dict(task_ref=c.record_ref(root/'jobs/002.json'),**anchor))
    config=e.configure(root,2,'resource_fixture:fixture',{},c.workspace(root,2)/'work/experiment-config.json')
    before=budget.execution_accounting(root)
    publish(config,pending=True,reason=REVIEW)
    from test_execution_resources import GateContractFixture
    def forbidden(*args,**kwargs):pytest.fail('Known review refusal reached allocation, policy or preparation')
    monkeypatch.setattr(GateContractFixture,'prepare',forbidden)
    from scidiscovery.research_loop import execution_authority
    monkeypatch.setattr(execution_authority,'_allocate_call',forbidden)
    result=_cli(root,capsys,'run','--payload',_source(root),'--research',_research_file(root,config))
    assert result['status']=='review_required' and result['accepted'] is False
    assert result['handoff']['reason']=='execution_not_accepted' and 'call_id' not in result['handoff']
    assert 'call_id' not in result and e.Records(config).calls()==[]
    assert not (root/'execution-authority/calls').exists()
    assert budget.execution_accounting(root)==before


def test_real_instance_admission_lock_contention_has_no_invented_call(tmp_path, monkeypatch, execution_registry, capsys):
    root,_,config,_=case(tmp_path,monkeypatch,executor='local_execution:command')
    def forbidden(*args,**kwargs):pytest.fail('Busy admission reached preparation')
    monkeypatch.setattr(LocalExecution,'prepare',forbidden)
    with e.Records(config).lease():
        result=_cli(root,capsys,'run','--script',_source(root),'--research',_research_file(root,config))
    assert result['status']=='known_not_started' and result['accepted'] is False
    assert 'call_id' not in result and 'call_id' not in result['handoff']
    assert result['handoff']['reason']=='execution_not_accepted'
    assert 'Reconcile this same original call' not in result['handoff']['instruction']
    assert e.Records(config).calls()==[] and _temporary(e.Records(config))==[]
    _no_host_preparation(root,config)
    assert execution_resume.capture_pending(root,c.read(root/'jobs/002.json')) is None


@pytest.mark.parametrize('cleanup_failure',[False,True])
def test_postprepare_local_refusal_cancels_only_current_temporary_preparation(tmp_path, monkeypatch, execution_registry, capsys, cleanup_failure):
    root,_,config,_=case(tmp_path,monkeypatch,executor='local_execution:command')
    original=LocalExecution.prepare
    prepared=[]
    def changed(self,payload,**options):
        result=original(self,payload,**options)
        prepared.append(payload.name)
        publish(config,pending=True,reason=REVIEW)
        return result
    monkeypatch.setattr(LocalExecution,'prepare',changed)
    remove=shutil.rmtree
    if cleanup_failure:
        def failed(path,*args,**kwargs):
            if Path(path).parent==e.Records(config).root:
                raise PermissionError('Injected failure removing this temporary local preparation')
            return remove(path,*args,**kwargs)
        monkeypatch.setattr(shutil,'rmtree',failed)
    result=_cli(root,capsys,'run','--script',_source(root),'--research',_research_file(root,config))
    assert len(prepared)==1 and result['accepted'] is False and 'call_id' not in result
    assert result['status']==('known_not_started' if cleanup_failure else 'review_required')
    if cleanup_failure:assert result['error']=='local_cleanup_failed'
    assert result['handoff']['reason']=='execution_not_accepted'
    assert 'call_id' not in result['handoff'] and e.Records(config).calls()==[]
    assert len(_temporary(e.Records(config)))==int(cleanup_failure)
    _no_host_preparation(root,config)
    assert execution_resume.capture_pending(root,c.read(root/'jobs/002.json')) is None
    publish(config,pending=False)
    class NextPreparationReached(Exception):pass
    def next_prepare(*args,**kwargs):raise NextPreparationReached()
    monkeypatch.setattr(LocalExecution,'prepare',next_prepare)
    with pytest.raises(NextPreparationReached):e.dispatch(config,_source(root,'next.py'),mechanism=_research(config))


def test_live_local_preparation_cancellation_removes_only_its_temporary_directory(tmp_path, monkeypatch, execution_registry):
    root,_,config,_=case(tmp_path,monkeypatch,executor='local_execution:command')
    neighbor=e.Records(config).root/'unrelated-local-file'
    neighbor.parent.mkdir(parents=True,exist_ok=True);neighbor.write_text('Preserve this unrelated original')
    prepare=LocalExecution.prepare
    with monkeypatch.context() as patch:
        def cancelled(*args,**kwargs):
            prepare(*args,**kwargs)
            raise KeyboardInterrupt('The live owner cancelled preparation before acceptance')
        patch.setattr(LocalExecution,'prepare',cancelled)
        with pytest.raises(KeyboardInterrupt):e.dispatch(config,_source(root),mechanism=_research(config))
    assert neighbor.read_text()=='Preserve this unrelated original'
    assert e.Records(config).calls()==[] and _temporary(e.Records(config))==[]
    _no_host_preparation(root,config)
    assert execution_resume.capture_pending(root,c.read(root/'jobs/002.json')) is None
    e.Records(config).preflight(expected_calls=[],mechanism=_research(config))


@pytest.mark.parametrize('error',[BlockingIOError,e.ConcurrentExecution,breadth.OpportunityRequired,e.ReviewRequired])
def test_third_party_prepare_effect_exception_remains_unknown(tmp_path, monkeypatch, remote_registry, capsys, error):
    root,_,config,_=case(tmp_path,monkeypatch)
    effect=tmp_path/'observed-external-prepare-effect'
    def changed(self,payload,**options):
        effect.write_text(payload.name)
        raise error('Observed external preparation effect before this exception')
    monkeypatch.setattr(DifferentExternalIdentity,'prepare',changed)
    result=_cli(root,capsys,'run','--payload',_source(root),'--new-plan','--research',_research_file(root,config),expected=3)
    assert effect.is_file() and result['status']=='unknown'
    assert result.get('accepted') is not False and result['handoff']['action']=='reconcile_or_stop'
    assert e.Records(config).calls()==[]
    assert not (e.Records(config).root/'host-preparations').exists()


@pytest.mark.parametrize('hook,error',[('submit',breadth.OpportunityRequired),('result',breadth.OpportunityRequired),('public_result',e.ReviewRequired)])
def test_accepted_provider_admission_error_preserves_original_call(tmp_path, monkeypatch, remote_registry, capsys, hook, error):
    root,_,config,_=case(tmp_path,monkeypatch)
    source=_source(root);effect=tmp_path/'observed-external-submit-effect'
    submitted=[];submit=DifferentExternalIdentity.submit
    def changed(self,descriptor):
        result=submit(self,descriptor)
        submitted.append(descriptor.name);effect.write_text(descriptor.name)
        if hook=='submit':raise error('Provider refused after the original external submission')
        return result
    monkeypatch.setattr(DifferentExternalIdentity,'submit',changed)
    if hook!='submit':
        original=getattr(DifferentExternalIdentity,hook)
        def refused(self,*args,**kwargs):
            original(self,*args,**kwargs)
            assert effect.is_file()
            raise error('Provider callback failed after the original external submission')
        monkeypatch.setattr(DifferentExternalIdentity,hook,refused)
    result=_cli(root,capsys,'run','--payload',source,'--new-plan','--research',_research_file(root,config))
    call_id=effect.read_text();records=e.Records(config);directory=records.directory(call_id)
    accepted=(directory/'accepted.json').read_bytes()
    assert c.read(directory/'remote-terminal.json')['call_id']==call_id
    assert c.read(directory/'completed.json')['status']=='known_success'
    assert result['status']=='unknown' and result['call_id']==call_id
    assert result.get('accepted') is not False and result['handoff']['call_id']==call_id
    assert result['handoff']['reason']!='execution_not_accepted'
    # The repeated request reports the same unknown original; a review error keeps exit 3.
    repeated=_cli(root,capsys,'run','--payload',source,'--new-plan','--research',_research_file(root,config),
        expected=3 if hook=='public_result' else 2)
    assert repeated['status']=='unknown' and repeated['call_id']==call_id
    assert repeated.get('accepted') is not False and repeated['handoff']['call_id']==call_id
    assert submitted==[call_id] and [path.name for path in records.calls()]==[call_id]
    assert (directory/'accepted.json').read_bytes()==accepted


def test_third_party_postprepare_admission_refusal_returns_exact_pending_call(tmp_path, monkeypatch, remote_registry, capsys):
    root,_,config,_=case(tmp_path,monkeypatch)
    source=_source(root);effect=tmp_path/'original-external-preparation'
    prepare=DifferentExternalIdentity.prepare
    def prepared(self,payload,**options):
        result=prepare(self,payload,**options)
        effect.write_text(payload.name)
        publish(config,pending=True)
        return result
    monkeypatch.setattr(DifferentExternalIdentity,'prepare',prepared)
    first=_cli(root,capsys,'run','--payload',source,'--new-plan','--research',_research_file(root,config))
    assert first['status']=='unknown' and first['call_id']==effect.read_text()
    assert first.get('accepted') is not False and first['handoff']['call_id']==first['call_id']
    assert first['handoff']['reason']!='execution_not_accepted'
    rows=preparation_recovery.retained_preparations(root,e.Records(config).root)
    assert len(rows)==1 and rows[0]['resolved'] is False and rows[0]['call_id']==first['call_id']
    assert e.Records(config).calls()==[] and not Path(rows[0]['snapshot']).exists()
    original=Path(rows[0]['binding']['path']).read_bytes()
    def forbidden(*args,**kwargs):pytest.fail('Unknown external preparation was admitted or prepared again')
    monkeypatch.setattr(DifferentExternalIdentity,'prepare',forbidden)
    repeated=_cli(root,capsys,'run','--payload',source,'--new-plan','--research',_research_file(root,config))
    assert repeated['status']=='unknown' and repeated['call_id']==first['call_id']
    assert Path(rows[0]['binding']['path']).read_bytes()==original


def test_bridge_keeps_verified_preparation_unknown_and_cached_retry_without_prepare(tmp_path, monkeypatch, remote_registry):
    from scidiscovery.research_loop import worker_execution_bridge as bridge
    root,_,config,_=case(tmp_path,monkeypatch)
    work=c.workspace(root,2);config_path=work/'work/experiment-config.json'
    directory=work/bridge.SUBMISSION_DIRECTORY;directory.mkdir(exist_ok=True)
    private=Path(config['record_dir']).parent
    claims=private/'worker-submissions';claims.mkdir(exist_ok=True)
    diagnostic=private/'bridge-diagnostics/latest-error.json';diagnostic.parent.mkdir(exist_ok=True)
    server=SimpleNamespace(work=work,directory=directory,config_path=config_path,configuration=config,
        client=c.read(config_path),claims=claims,diagnostic=diagnostic,record=execution.origin(root,2),
        diagnostic_clean=None,
        stop=threading.Event(),environment={},launch=lambda *args:pytest.fail('External preparation cannot launch a local process'))
    effect=tmp_path/'bridge-external-preparation';prepare=DifferentExternalIdentity.prepare
    def prepared(self,payload,**options):
        result=prepare(self,payload,**options)
        with effect.open('a') as stream:stream.write(payload.name+'\n')
        publish(config,pending=True)
        return result
    monkeypatch.setattr(DifferentExternalIdentity,'prepare',prepared)
    request=dict(submission_ref='S001',arguments=['run','--payload',str(_source(root)),'--new-plan','--research',_research_file(root,config)])
    raw=bridge._encoded(request);bridge._handle(server,raw)
    first=c.read(directory/'response.json');call=effect.read_text().strip()
    assert first['code']==2 and first['result']['status']=='unknown' and first['result']['call_id']==call
    assert first['result']['handoff']['call_id']==call
    assert first['result'].get('accepted') is not False
    outcome=claims/'S001/outcome.json'
    assert c.read(outcome)['calls']==[] and e.Records(config).calls()==[]
    originals={claims/'S001'/name:(claims/'S001'/name).read_bytes()
        for name in ('submit.json','binding.json','response.json','outcome.json')}
    def forbidden(*args,**kwargs):pytest.fail('Bridge replayed an unresolved external preparation')
    monkeypatch.setattr(DifferentExternalIdentity,'prepare',forbidden)
    bridge._handle(server,raw)
    cached=c.read(directory/'response.json')
    assert cached['code']==2 and cached['result']['status']=='unknown' and cached['result']['call_id']==call
    assert all(path.read_bytes()==value for path,value in originals.items())
    bridge._handle(server,bridge._encoded(dict(request,submission_ref='S002')))
    repeated=c.read(directory/'response.json')
    assert repeated['code']==2 and repeated['result']['status']=='unknown' and repeated['result']['call_id']==call
    assert effect.read_text().splitlines()==[call]
    assert not diagnostic.exists()
    with pytest.raises(e.IntegrityError):
        bridge._response_calls(server,request,dict(result=dict(status='unknown',call_id='unbound-missing-call')),set())


@pytest.mark.process_e2e
@pytest.mark.parametrize('error,expected',[
    pytest.param('ConcurrentExecution',2,id='concurrent'),
    pytest.param('OpportunityRequired',3,id='opportunity'),
    pytest.param('ReviewRequired',3,id='review'),
])
def test_module_cli_canonical_prepare_conflict_after_effect_stays_unknown(tmp_path, monkeypatch, remote_registry, error, expected):
    plugin=tmp_path/'uncertain-plugin';plugin.mkdir()
    (plugin/'uncertain_prepare.py').write_text('''import json
from pathlib import Path
from execution_fixtures import DifferentExternalIdentity
from scidiscovery.operations.execution import ExecutionInterfaceRegistration
from scidiscovery.operations.runtime_plugins import RuntimePluginContribution,RuntimePluginFactory
from scidiscovery.research_loop import breadth,experiments as e
class UncertainPreparation(DifferentExternalIdentity):
    def prepare(self,payload,**options):
        with Path(self.config['effect']).open('a') as stream:stream.write(payload.name+'\\n')
        error=breadth.OpportunityRequired if self.config['error']=='OpportunityRequired' else getattr(e,self.config['error'])
        raise error('Original preparation may already have external effects')
def build(context):
    return RuntimePluginContribution(execution_adapters={'fixture':UncertainPreparation(json.loads(context.config_bytes))})
REGISTRATION=ExecutionInterfaceRegistration('uncertain_prepare','1',RuntimePluginFactory(build))
''')
    metadata=plugin/'uncertain_prepare.dist-info';metadata.mkdir()
    (metadata/'METADATA').write_text('Metadata-Version: 2.1\nName: uncertain-prepare\nVersion: 0\n')
    (metadata/'entry_points.txt').write_text('[scidiscovery.execution_interfaces]\nuncertain_prepare = uncertain_prepare:REGISTRATION\n')
    monkeypatch.syspath_prepend(str(plugin))
    monkeypatch.setenv('PYTHONPATH',str(plugin)+os.pathsep+os.environ.get('PYTHONPATH',''))
    effect=tmp_path/'observed-module-prepare-effect'
    root,_,config,_=case(tmp_path,monkeypatch,executor='uncertain_prepare:fixture',extra=dict(effect=str(effect),error=error))
    result=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.experiments',
        '--config',str(c.workspace(root,2)/'work/experiment-config.json'),'run','--payload',str(_source(root)),
        '--new-plan','--research',_research_file(root,config)],capture_output=True,text=True,timeout=15)
    assert result.returncode==expected,(result.stdout,result.stderr)
    assert not result.stderr
    response=json.loads(result.stdout)
    assert response['status']=='unknown' and response.get('accepted') is not False
    assert response['handoff']['action']=='reconcile_or_stop'
    assert len(effect.read_text().splitlines())==1 and e.Records(config).calls()==[]


@pytest.mark.process_e2e
def test_competing_cli_refuses_before_prepare_and_query_can_run_during_submit(tmp_path, monkeypatch, remote_registry):
    plugin=tmp_path/'coordinated-plugin';plugin.mkdir()
    (plugin/'coordinated_prepare.py').write_text('''import json,time
from pathlib import Path
from execution_fixtures import DifferentExternalIdentity
from scidiscovery.operations.execution import ExecutionInterfaceRegistration
from scidiscovery.operations.runtime_plugins import RuntimePluginContribution,RuntimePluginFactory
from scidiscovery.research_loop import experiments as e
class CoordinatedPreparation(DifferentExternalIdentity):
    def entered(self,stage,call_id):
        directory=Path(self.config['gates'])
        with (directory/'events.jsonl').open('a') as stream:stream.write(json.dumps([stage,call_id])+'\\n')
        (directory/(stage+'-ready')).write_text(call_id)
    def wait(self,stage):
        release=Path(self.config['gates'])/(stage+'-release')
        deadline=time.monotonic()+20
        while not release.exists():
            if time.monotonic()>=deadline:raise TimeoutError('Bounded fixture handshake expired')
            time.sleep(.02)
    def prepare(self,payload,**options):
        self.entered('prepare',payload.name);self.wait('prepare')
        return super().prepare(payload,**options)
    def acceptance_metadata(self,payload,**options):
        self.entered('metadata',payload.name)
        return super().acceptance_metadata(payload,**options)
    def submit(self,payload):
        self.entered('submit',payload.name);self.wait('submit')
        return super().submit(payload)
    @e.record_effect
    def status_details(self,call_id):
        if not (self.records.directory(call_id)/'completed.json').exists():
            return dict(state='running',call_id=call_id)
        return super().status_details(call_id)
def build(context):
    return RuntimePluginContribution(execution_adapters={'fixture':CoordinatedPreparation(json.loads(context.config_bytes))})
REGISTRATION=ExecutionInterfaceRegistration('coordinated_prepare','1',RuntimePluginFactory(build))
''')
    metadata=plugin/'coordinated_prepare.dist-info';metadata.mkdir()
    (metadata/'METADATA').write_text('Metadata-Version: 2.1\nName: coordinated-prepare\nVersion: 0\n')
    (metadata/'entry_points.txt').write_text('[scidiscovery.execution_interfaces]\ncoordinated_prepare = coordinated_prepare:REGISTRATION\n')
    monkeypatch.syspath_prepend(str(plugin))
    monkeypatch.setenv('PYTHONPATH',str(plugin)+os.pathsep+os.environ.get('PYTHONPATH',''))
    gates=tmp_path/'gates';gates.mkdir()
    root,_,config,_=case(tmp_path,monkeypatch,executor='coordinated_prepare:fixture',extra=dict(gates=str(gates)))
    client=c.workspace(root,2)/'work/experiment-config.json';source=_source(root)
    command=[sys.executable,'-m','scidiscovery.research_loop.experiments','--config',str(client)]
    run=['run','--payload',str(source),'--new-plan','--research',_research_file(root,config)]
    records=e.Records(config);events=budget._events(root,c.settings(root))[0]
    winner=subprocess.Popen([*command,*run],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    def ready(stage):
        path=gates/(stage+'-ready');deadline=time.monotonic()+10
        while not path.exists():
            assert winner.poll() is None,'The winning fixture exited before its handshake'
            assert time.monotonic()<deadline,'The winning fixture did not reach its bounded handshake'
            time.sleep(.02)
        return path.read_text()
    try:
        call_id=ready('prepare')
        loser=subprocess.run([*command,*run],capture_output=True,text=True,timeout=10)
        assert loser.returncode==2 and not loser.stderr,(loser.stdout,loser.stderr)
        refused=json.loads(loser.stdout)
        assert refused['status']=='known_not_started' and refused['accepted'] is False
        assert 'call_id' not in refused and refused['handoff']['reason']=='execution_not_accepted'
        assert [json.loads(row) for row in (gates/'events.jsonl').read_text().splitlines()]==[['prepare',call_id]]
        assert records.calls()==[] and not (records.root/'host-preparations').exists()
        assert budget._events(root,c.settings(root))[0]==events
        assert not (root/'execution-authority/calls').exists()
        (gates/'prepare-release').touch()
        assert ready('submit')==call_id
        query=subprocess.run([*command,'query','--call-id',call_id],capture_output=True,text=True,timeout=10)
        assert query.returncode==0 and not query.stderr,(query.stdout,query.stderr)
        observed=json.loads(query.stdout)
        assert observed['state']=='running' and observed['call_id']==call_id
        assert winner.poll() is None and not (gates/'submit-release').exists()
        (gates/'submit-release').touch()
        stdout,stderr=winner.communicate(timeout=10)
        assert winner.returncode==0 and not stderr,(stdout,stderr)
        completed=json.loads(stdout)
        assert completed['status']=='known_success' and completed['call_id']==call_id
        assert [json.loads(row) for row in (gates/'events.jsonl').read_text().splitlines()]==[
            ['prepare',call_id],['metadata',call_id],['submit',call_id]]
        assert [path.name for path in records.calls()]==[call_id]
        assert all(row['resolved'] for row in preparation_recovery.retained_preparations(root,records.root))
        assert _temporary(records)==[]
    finally:
        (gates/'prepare-release').touch();(gates/'submit-release').touch()
        if winner.poll() is None:
            winner.kill();winner.communicate(timeout=5)


@pytest.mark.process_e2e
@pytest.mark.parametrize('boundary',['prepare','copy'])
def test_terminal_lifecycle_remains_available_during_next_preparation(tmp_path, monkeypatch, remote_registry, boundary):
    plugin=tmp_path/'shared-lifecycle-plugin';plugin.mkdir()
    (plugin/'shared_lifecycle.py').write_text('''import json
from execution_fixtures import DifferentExternalIdentity
from scidiscovery.operations.execution import ExecutionInterfaceRegistration
from scidiscovery.operations.runtime_plugins import RuntimePluginContribution,RuntimePluginFactory
from scidiscovery.research_loop import experiments as e
class SharedLifecycle(DifferentExternalIdentity):
    @e.record_effect
    def status_details(self,call_id):return super().status_details(call_id)
    @e.record_effect
    def result(self,call_id):return super().result(call_id)
    @e.record_effect
    def collect_original(self,call_id):return super().collect_original(call_id)
def build(context):
    return RuntimePluginContribution(execution_adapters={'fixture':SharedLifecycle(json.loads(context.config_bytes))})
REGISTRATION=ExecutionInterfaceRegistration('shared_lifecycle','1',RuntimePluginFactory(build))
''')
    metadata=plugin/'shared_lifecycle.dist-info';metadata.mkdir()
    (metadata/'METADATA').write_text('Metadata-Version: 2.1\nName: shared-lifecycle\nVersion: 0\n')
    (metadata/'entry_points.txt').write_text('[scidiscovery.execution_interfaces]\nshared_lifecycle = shared_lifecycle:REGISTRATION\n')
    monkeypatch.syspath_prepend(str(plugin))
    monkeypatch.setenv('PYTHONPATH',str(plugin)+os.pathsep+os.environ.get('PYTHONPATH',''))
    root,_,config,_=case(tmp_path,monkeypatch,executor='shared_lifecycle:fixture')
    source=_source(root)
    first=e.dispatch(config,source,new=True,mechanism=_research(config))
    assert first['status']=='known_success'
    records=e.Records(config);first_id=first['call_id']
    accepted=(records.directory(first_id)/'accepted.json').read_bytes()
    client=c.workspace(root,2)/'work/experiment-config.json'
    gates=tmp_path/'preparation-gates';gates.mkdir()
    writer_source=r'''
import json,sys,time
from pathlib import Path
from scidiscovery.research_loop import contracts as c,experiments as e
from shared_lifecycle import SharedLifecycle
client,payload=map(Path,sys.argv[1:3]);gates=Path(sys.argv[3])
boundary,role,research=sys.argv[4:7];call_id=None
def stop():
    (gates/'ready').write_text(call_id)
    deadline=time.monotonic()+20
    while not (gates/'release').exists():
        if time.monotonic()>=deadline:raise TimeoutError('Bounded preparation handshake expired')
        time.sleep(.02)
prepare=SharedLifecycle.prepare
def prepared(self,payload,**options):
    global call_id
    call_id=payload.name
    with (gates/'events.jsonl').open('a') as stream:stream.write(json.dumps([role,'prepare',call_id])+'\n')
    if role=='winner' and boundary=='prepare':stop()
    return prepare(self,payload,**options)
SharedLifecycle.prepare=prepared
copy=c._bind_original
def copied(*args,**kwargs):
    result=copy(*args,**kwargs);target=Path(args[1])
    if role=='winner' and boundary=='copy' and target.name=='source.py' and target.parent.name.startswith('.prepared-'):
        stop()
    return result
c._bind_original=copied
raise SystemExit(e.main(['--config',str(client),'run','--payload',str(payload),'--new-plan','--research',research]))
'''
    command=[sys.executable,'-m','scidiscovery.research_loop.experiments','--config',str(client)]
    writer=[sys.executable,'-c',writer_source,str(client),str(source),str(gates),boundary]
    research=_research_file(root,config,'Q')
    winner=subprocess.Popen([*writer,'winner',research],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+10
        while not (gates/'ready').exists():
            assert winner.poll() is None,'The writer exited before its preparation handshake'
            assert time.monotonic()<deadline,'The writer did not reach its bounded preparation handshake'
            time.sleep(.02)
        call_id=(gates/'ready').read_text()
        assert call_id!=first_id and [path.name for path in records.calls()]==[first_id]
        competitor=subprocess.run([*writer,'competitor',research],capture_output=True,text=True,timeout=5)
        assert competitor.returncode==2 and not competitor.stderr,(competitor.stdout,competitor.stderr)
        refused=json.loads(competitor.stdout)
        assert [json.loads(row) for row in (gates/'events.jsonl').read_text().splitlines()]==[['winner','prepare',call_id]]
        query=subprocess.run([*command,'query','--call-id',first_id],capture_output=True,text=True,timeout=5)
        assert query.returncode==0 and not query.stderr,(query.stdout,query.stderr)
        observed=json.loads(query.stdout)
        assert observed['state']=='succeeded' and observed['call_id']==first_id
        result=subprocess.run([sys.executable,'-c',
            'import json,sys;from pathlib import Path;from scidiscovery.research_loop import contracts as c,experiments as e;'
            'print(json.dumps(e.load(c.read(Path(sys.argv[1]))).result(sys.argv[2])))',
            str(execution.record(root,2)/'experiment-config.json'),first_id],capture_output=True,text=True,timeout=5)
        assert result.returncode==0 and not result.stderr,(result.stdout,result.stderr)
        original=json.loads(result.stdout)
        assert original['status']=='known_success' and original['call_id']==first_id
        collected=subprocess.run([*command,'collect','--call-id',first_id],capture_output=True,text=True,timeout=5)
        assert collected.returncode==0 and not collected.stderr,(collected.stdout,collected.stderr)
        assert json.loads(collected.stdout)['status']=='collected'
        if boundary=='prepare':
            assert refused['status']=='known_not_started' and refused['accepted'] is False
            assert 'call_id' not in refused and refused['handoff']['reason']=='execution_not_accepted'
        else:
            assert refused['status']=='unknown' and refused['call_id']==call_id
            assert refused.get('accepted') is not False and refused['handoff']['action']=='reconcile_or_stop'
        assert winner.poll() is None and not (gates/'release').exists()
        assert [path.name for path in records.calls()]==[first_id]
        assert (records.directory(first_id)/'accepted.json').read_bytes()==accepted
        assert [json.loads(row) for row in (gates/'events.jsonl').read_text().splitlines()]==[['winner','prepare',call_id]]
        (gates/'release').touch()
        stdout,stderr=winner.communicate(timeout=10)
        assert winner.returncode==0 and not stderr,(stdout,stderr)
        completed=json.loads(stdout)
        assert completed['status']=='known_success' and completed['call_id']==call_id
        assert [path.name for path in records.calls()]==[first_id,call_id]
        assert _temporary(records)==[]
        assert all(row['resolved'] for row in preparation_recovery.retained_preparations(root,records.root))
    finally:
        (gates/'release').touch()
        if winner.poll() is None:winner.kill()
        winner.communicate(timeout=5)


def test_accepted_local_without_process_or_submission_remains_original_unknown(tmp_path, monkeypatch, execution_registry, capsys):
    root,_,config,_=case(tmp_path,monkeypatch,executor='local_execution:command')
    def uncertain(*args,**kwargs):raise BlockingIOError('Original submission acknowledgement is unavailable')
    monkeypatch.setattr(LocalExecution,'submit',uncertain)
    source=_source(root)
    first=_cli(root,capsys,'run','--script',source,'--research',_research_file(root,config))
    assert first['status']=='unknown' and len(e.Records(config).calls())==1
    call=first['call_id'];directory=e.Records(config).directory(call)
    accepted=(directory/'accepted.json').read_bytes()
    assert not (directory/'process.json').exists() and not (directory/'submission.json').exists()
    _no_host_preparation(root,config)
    def forbidden(*args,**kwargs):pytest.fail('Accepted unknown was prepared or submitted again')
    monkeypatch.setattr(LocalExecution,'prepare',forbidden)
    monkeypatch.setattr(LocalExecution,'submit',forbidden)
    repeat=_cli(root,capsys,'run','--script',source,'--research',_research_file(root,config,'Q'))
    assert repeat['status']=='unknown' and repeat['call_id']==call
    assert _cli(root,capsys,'query','--call-id',call,expected=0)['state']=='unknown'
    assert _cli(root,capsys,'cancel','--call-id',call)['status']=='unavailable'
    assert (directory/'accepted.json').read_bytes()==accepted and len(e.Records(config).calls())==1


def test_preadmission_local_race_keeps_competitor_identity_when_query_is_unavailable(tmp_path, monkeypatch, execution_registry, capsys):
    root,_,config,_=case(tmp_path,monkeypatch,executor='local_execution:command')
    original_state=e._original_dispatch_state
    winner='0001-accepted-competitor'
    def competing(settings,payload,**options):
        result=original_state(settings,payload,**options)
        adapter,_,_=result
        original=payload.model_copy(update={'name':winner})
        prepared=adapter.prepare(original,preparation_profile='research_loop.v1',exchange_directory=e.Records(config).root)
        e.Records(config).accept(prepared,adapter.acceptance_metadata(prepared),config['execution_interface'],
            expected_calls=[],mechanism=_research(config),_local_preparation=True)
        return result
    def unavailable(*args,**kwargs):raise OSError('Current original status observation is unavailable')
    def forbidden(*args,**kwargs):pytest.fail('A losing admission submitted another execution')
    monkeypatch.setattr(e,'_original_dispatch_state',competing)
    monkeypatch.setattr(LocalExecution,'status_details',unavailable)
    monkeypatch.setattr(LocalExecution,'submit',forbidden)
    result=_cli(root,capsys,'run','--script',_source(root),'--research',_research_file(root,config))
    assert result['status']=='unknown' and result['call_id']==winner
    assert result['handoff']['call_id']==winner
    assert [path.name for path in e.Records(config).calls()]==[winner]
    assert _temporary(e.Records(config))==[]
    _no_host_preparation(root,config)


@pytest.mark.process_e2e
def test_real_native_next_legal_local_call_after_preaccept_review_cancellation(tmp_path, monkeypatch):
    import test_default_native_local_execution as native_case
    cancelled=tmp_path/'local-preparation-cancelled'
    installed=tmp_path/'runtime-fault-installed'
    _runtime_owner_fault(monkeypatch,f'''from pathlib import Path
from scidiscovery.research_loop import budget,contracts as c,experiments as e
from scidiscovery.research_loop.local_experiments import LocalExecution
cancelled=Path({str(cancelled)!r})
Path({str(installed)!r}).write_text('Actual runtime owner installed the admission fault')
prepare=LocalExecution.prepare;accept=e.Records.accept
def publish(config,pending):
    stamp=budget.clock_stamp()
    c.write(Path(config['admission']['directory'])/'review.json',dict(version=1,
        scope=config['scope'],stage_anchor=config['admission']['stage_anchor'],
        native_attempt=config['native_attempt'],boot_id=stamp['boot_id'],
        deadline_boottime=stamp['at_boottime']+60,valid_until_boottime=stamp['at_boottime']+60,
        pending=pending,reason='reserve_reached' if pending else None,inflight_usage_unknown=True))
def changed(self,payload,**options):
    result=prepare(self,payload,**options)
    if not cancelled.exists():publish(self.config,True)
    return result
def refused(self,*args,**kwargs):
    try:return accept(self,*args,**kwargs)
    except e.ReviewRequired:
        publish(self.config,False);cancelled.write_text('Definite local refusal before submission')
        raise
LocalExecution.prepare=changed;e.Records.accept=refused
''')
    standin=native_case.NATIVE_STANDIN
    for old,new in ((5,6),(4,5),(3,4)):
        standin=standin.replace(f'request({old},[',f'request({new},[')
    standin=standin.replace("reply=request(2,arguments)\nassert reply['code']==0,reply",
        "refused=request(2,arguments)\n"
        "assert refused['code']==2 and refused['result']['status']=='review_required',refused\n"
        "assert refused['result']['accepted'] is False and 'call_id' not in refused['result'],refused\n"
        "assert refused['result']['handoff']['reason']=='execution_not_accepted',refused\n"
        "reply=request(3,arguments)\nassert reply['code']==0,reply")
    monkeypatch.setattr(native_case,'NATIVE_STANDIN',standin)
    native_case._default_local_case(tmp_path,monkeypatch,True,False)
    root=tmp_path/'instance';config=c.read(execution.origin(root,2)/'experiment-config.json')
    assert installed.is_file() and cancelled.is_file() and len(e.Records(config).calls())==1
    assert _temporary(e.Records(config))==[]
    _no_host_preparation(root,config)


@pytest.mark.process_e2e
def test_real_native_racing_local_acceptance_returns_exact_winner_and_starts_once(tmp_path, monkeypatch):
    import test_default_native_local_execution as native_case
    winner=tmp_path/'winning-local-call'
    installed=tmp_path/'runtime-fault-installed'
    _runtime_owner_fault(monkeypatch,f'''from pathlib import Path
from scidiscovery.research_loop import experiments as e
winner=Path({str(winner)!r})
Path({str(installed)!r}).write_text('Actual runtime owner installed the admission race')
original_state=e._original_dispatch_state;entered=False
def competing(config,payload,**options):
    global entered
    result=original_state(config,payload,**options)
    if not entered:
        entered=True
        prior=e.dispatch(config,Path(payload.local_path),command_options=dict(
            experiment_directory=str(Path(payload.local_path).parent),arguments=[]))
        assert prior['status']=='known_success'
        winner.write_text(prior['call_id'])
    return result
e._original_dispatch_state=competing
''')
    native_case._default_local_case(tmp_path,monkeypatch,True,False)
    root=tmp_path/'instance';config=c.read(execution.origin(root,2)/'experiment-config.json')
    records=e.Records(config);calls=records.calls()
    assert installed.is_file() and len(calls)==1 and calls[0].name==winner.read_text()
    assert (calls[0]/'process.json').is_file() and (calls[0]/'completed.json').is_file()
    assert _temporary(records)==[]
    _no_host_preparation(root,config)
