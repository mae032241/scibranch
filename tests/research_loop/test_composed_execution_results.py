"""Opaque private results and explicit async terminal reconciliation, without models."""
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, execution, experiments as e, history, native, search
from test_history_handoff import finish


# Every case exercises the real experiments CLI in separate local processes.
pytestmark = pytest.mark.process_e2e


@pytest.fixture
def composed_registry(tmp_path,monkeypatch):
    if list(importlib.metadata.entry_points().select(group='scidiscovery.execution_interfaces',name='composed_execution')):return
    metadata=tmp_path/'registration/composed_contract.dist-info';metadata.mkdir(parents=True)
    (metadata/'METADATA').write_text('Metadata-Version: 2.1\nName: composed-contract\nVersion: 0\n')
    (metadata/'entry_points.txt').write_text('[scidiscovery.execution_interfaces]\ncomposed_execution = composed_execution_fixture:REGISTRATION\n')
    monkeypatch.syspath_prepend(str(metadata.parent))
    monkeypatch.setenv('PYTHONPATH',os.pathsep.join([str(metadata.parent),str(Path(__file__).parent),str(Path(e.__file__).parents[2])]))


def case(tmp_path,options,observer=None):
    public=tmp_path/'public';public.mkdir();goal=public/'objective.md';goal.write_text('Synthetic execution contract only.')
    root=tmp_path/'instance'
    extra={}
    if observer:
        adapter=tmp_path/'observer.py'
        adapter.write_text('import json,sys\njson.load(sys.stdin)\n'+('print("invalid")\n' if observer=='broken' else
            'print(json.dumps(dict(candidates=[],current_candidate=None,actual_submission=None,coverage=dict(status="complete",note="No domain claims"))))\n'))
        extra['history_observer']=dict(adapter=str(adapter),config={})
    controller.prepare(root,goal,public,['never-launched'],max_tokens=10000,max_seconds=120,max_steps=10,
        model='no-model',reasoning_effort='high',node_budget_mode='soft',experiment_mode='external',
        # This fixture binds an H catalogue, not v3 live W declarations.
        research_strategy_version=1,**extra)
    finish(root,1,'H',[],action='experiment',search_choice=dict(scope='Two synthetic mechanisms',commitments=[
        dict(label=label,minimal_test='Read the exact backend observation',existing_ref=None) for label in ('P','Q')],
        focus=dict(index=1,action='screen')))
    job=c.build_job(root,2,'W',[1]);record=execution.record(root,2)
    accepted_budget.accept_native(root,2,record,dict(task_ref=c.record_ref(root/'jobs/002.json'),
        search_slice=search.accept(root,job)))
    path=c.workspace(root,2)/'work/config.json';config=e.configure(root,2,'composed_execution:fixture',options,path)
    package=c.workspace(root,2)/'code/package';package.parent.mkdir(exist_ok=True)
    package.write_bytes(b'opaque original job package')
    def cli(*args,expected=0):
        reply=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.experiments','--config',str(path),*map(str,args)],capture_output=True,text=True)
        assert reply.returncode==expected,(reply.stdout,reply.stderr)
        return json.loads(reply.stdout)
    started=cli('run','--payload',package,'--new-plan','--mechanism','1',expected=0 if options.get('synchronous') else 2)
    return root,job,config,cli,started['call_id']


def seal_and_read(root,job,observed=True):
    finish(root,2,'W',[1],preserve_native_intent=True,mechanism_findings=[
        dict(mechanism='P',basis='observed' if observed else 'blocked',finding='Synthetic backend fact; no scientific qualification.'),
        dict(mechanism='Q',basis='blocked',finding='Not executed in this fixture.')])
    seal=c.read(execution.record(root,2)/'result-identity.json');fact=c.read(root/seal['observations']['path'])
    a=c.build_job(root,3,'A',[1,2]);page=a['history']['view']['indexes'].get('experiments');calls=[]
    while page:
        index=c.read(c.workspace(root,3)/page);calls+=index['experiments'];page=index['next_page']
    for row in calls:
        if 'outputs' not in row:
            assert row['execution_status']=='unknown'
            assert 'result_status' not in row
            continue
        for output in row['outputs']:assert (c.workspace(root,3)/output['path']).read_bytes()==b'opaque original job package'
    return fact,calls


@pytest.mark.parametrize('observer,private',[
    (None,None),(None,'native'),('empty','local-like'),('broken','local-like'),
])
def test_opaque_private_remote_results_seal_and_read_without_candidate_inference(tmp_path,composed_registry,observer,private):
    root,job,config,cli,call=case(tmp_path,dict(synchronous=True,private=private),observer)
    assert config['execution_interface']['result_protocol'] is None
    assert cli('query','--call-id',call)['state']=='succeeded'
    fact,calls=seal_and_read(root,job)
    assert fact['managed_calls'][0]['status']=='known_success' and fact['candidates']==[]
    assert not fact.get('candidate_diagnostics')
    completed=c.read(root/fact['managed_calls'][0]['completed']['snapshot'])
    if private:assert completed['result']['candidates']
    assert len(calls)==1 and len(calls[0]['outputs'])==1


@pytest.mark.parametrize('observer,private',[(None,'native'),('broken','local-like')])
def test_explicit_bad_candidate_protocol_is_bounded_diagnostic_not_scientific_candidate(tmp_path,composed_registry,observer,private):
    root,job,config,cli,call=case(tmp_path,dict(synchronous=True,private=private,result_protocol=e.LOCAL_RESULT_PROTOCOL),observer)
    fact,calls=seal_and_read(root,job)
    assert fact['managed_calls'][0]['status']=='known_success' and fact['candidates']==[]
    assert fact['candidate_diagnostics']==[dict(call_id=call,code='declared_candidate_mapping_invalid')]


@pytest.mark.parametrize('outcome,state',[('known_success','succeeded'),('known_failed','failed'),('known_failed','cancelled')])
def test_async_without_collect_reconciles_same_call_before_seal(tmp_path,composed_registry,outcome,state):
    options=dict(collect=False,terminal_status=outcome)
    if state=='cancelled':options['terminal_state']=state
    root,job,config,cli,call=case(tmp_path,options)
    help_reply=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.experiments','--help'],capture_output=True,text=True)
    assert help_reply.returncode==0 and 'reconcile' in help_reply.stdout and 'never submit' in help_reply.stdout
    directory=e.Records(config).directory(call)
    assert cli('reconcile','--call-id',call,expected=2)['status']=='running'
    assert len(e.Records(config).calls())==1 and not (directory/'execution-observation.json').exists()
    e.load(config).complete(call)
    before={p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    assert cli('query','--call-id',call)['state']==state
    assert cli('lookup','--call-id',call)['state']==state
    assert before=={p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    assert cli('collect','--call-id',call,expected=2)['status']=='unavailable'
    assert cli('reconcile','--call-id',call)['status']==outcome
    observed=c.read(directory/'execution-observation.json')
    assert observed['outputs']==[] and not (directory/'terminal.json').exists() and not (directory/'process.json').exists()
    if state=='cancelled':
        assert observed['native_state']=='cancelled'
        for changed in (dict(observed,native_state='failed'),{key:value for key,value in observed.items() if key!='native_state'}):
            with pytest.raises(e.IntegrityError,match='native terminal state'):
                e.verify_observation(changed,lambda item:Path(item['path']))
    assert len(e.Records(config).calls())==1
    fact,calls=seal_and_read(root,job)
    assert fact['managed_calls'][0]['status']==outcome and fact['managed_calls'][0]['outputs']==[]
    if state=='cancelled':
        assert fact['managed_calls'][0]['execution']['native_state']==calls[0]['native_state']=='cancelled'
        assert fact['candidates']==[]
    (e.Records(config).root/'forbid-backend-reads').write_text('Repeated reconciliation must reuse the original projection.')
    before={p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    assert cli('reconcile','--call-id',call)['status']==outcome
    c.validate(c.workspace(root,2))
    assert before=={p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    # A evaluates the frozen fact; subsequent work can be admitted without replay.
    record=execution.record(root,3)
    payload=dict(summary='Assess original backend terminal',scientific_result='Synthetic facts only.',
        limitations=['No scientific qualification.'],implementation_selection=None,candidate_recommendation=None,
        progress_assessment=None,portfolio_review=None,search_review=dict(
            dispositions=[dict(mechanism_ref='h001.m1',status='screened',reason='Exact completed observation')],
            next_focus=dict(mechanism_ref='h001.m1',action='improve')),
        next_action='continue_w',route_reason='Improve the observed mechanism',
        decision_request=dict(decision='mechanism',question='What changes the mechanism choice?',change_criterion='A discriminating observation'))
    accepted_budget.accept_native(root,3,record,dict(task_ref=c.record_ref(root/'jobs/003.json')))
    c.write(record/'science-output.json',payload);c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,native_started=False,
        usage=native.usage(root),raw_ref=c.file_ref(record/'science-output.json')))
    c.seal_result(root,3)
    assert search.project(root,4)['mechanisms']['h001.m1']['status']=='screened'
    successor_job=c.build_job(root,4,'W',[1,2,3]);record=execution.record(root,4)
    node_anchor=budget.accept_node(root,successor_job)
    anchor=dict(budget_slice=node_anchor) if node_anchor is not None else dict(search_slice=search.accept(root,successor_job))
    accepted_budget.accept_native(root,4,record,dict(task_ref=c.record_ref(root/'jobs/004.json'),**anchor))
    successor=e.configure(root,4,'composed_execution:fixture',dict(synchronous=True),c.workspace(root,4)/'work/config.json')
    assert not successor['breadth']['prior_unknown']
    package=c.workspace(root,4)/'code/package';package.parent.mkdir(exist_ok=True)
    package.write_bytes(b'new opaque input')
    assert e.dispatch(successor,package,new=True,mechanism='1')['status']=='known_success'


def test_late_reconcile_preserves_old_sealed_unknown_without_explicit_recovery(tmp_path,composed_registry,monkeypatch):
    root,job,config,cli,call=case(tmp_path,dict(collect=False))
    from scidiscovery.research_loop import managed_wait
    # Retained historical pre-wait seal: only the fixture bypasses the new seal gate.
    with monkeypatch.context() as patch:
        patch.setattr(managed_wait,'before_seal',lambda *args,**kwargs:None)
        fact,calls=seal_and_read(root,job,observed=False)
    record=execution.record(root,2);seal_bytes=(record/'result-identity.json').read_bytes()
    assert len(fact['managed_calls'])==len(calls)==1
    original=fact['managed_calls'][0]
    assert original['call_id']==calls[0]['call_id']==call
    assert original['status']=='unknown' and original['completed'] is None and original['terminal'] is None
    assert not {'execution','outputs','observation'} & original.keys()
    assert calls[0]['execution_status']=='unknown' and 'outputs' not in calls[0]
    seal=c.read(record/'result-identity.json');fact_path=root/seal['observations']['path']
    fact_bytes=fact_path.read_bytes()
    successor=c.read(root/'jobs/003.json')
    page_path=c.workspace(root,3)/successor['history']['view']['indexes']['experiments']
    page_bytes=page_path.read_bytes();page=json.loads(page_bytes)
    assert page['experiments']==calls and page['next_page'] is None
    e.load(config).complete(call);assert cli('reconcile','--call-id',call)['status']=='known_success'
    assert history._observe_managed_calls(root,job,call_ids=[call])[0]['status']=='known_success'
    c.validate(c.workspace(root,2))
    assert (record/'result-identity.json').read_bytes()==seal_bytes
    assert fact_path.read_bytes()==fact_bytes and page_path.read_bytes()==page_bytes
    assert c.read(fact_path)['managed_calls'][0]['status']=='unknown'
    assert c.read(page_path)['experiments'][0]['execution_status']=='unknown'


@pytest.mark.parametrize('fault',['accepted-interface','external-id','backend-proof','backend-acceptance'])
def test_reconcile_rejects_changed_original_identity_and_native_evidence(tmp_path,composed_registry,fault):
    root,job,config,cli,call=case(tmp_path,dict(collect=False))
    e.load(config).complete(call);directory=e.Records(config).directory(call)
    path=directory/({'accepted-interface':'accepted.json','external-id':'submission.json','backend-proof':'backend-terminal.json','backend-acceptance':'backend-accepted.json'}[fault])
    value=c.read(path)
    if fault=='accepted-interface':value['interface']=dict(value['interface'],result_protocol=e.LOCAL_RESULT_PROTOCOL)
    elif fault=='external-id':value['external_run_id']='remote-other'
    elif fault=='backend-proof':value['scope']=dict(value['scope'],execution_key=dict(path='/fixture/native-evidence/002/native-intent.json'))
    else:value['prepared_ref']='f'*64
    c.write(path,value)
    assert cli('reconcile','--call-id',call,expected=3)['status']=='integrity_error'
    assert len(e.Records(config).calls())==1 and not (directory/'execution-observation.json').exists()


def test_pending_and_unknown_originals_block_new_calls_without_fabricated_running(tmp_path,composed_registry):
    root,job,config,cli,call=case(tmp_path,dict(collect=False,query_state='queued'))
    directory=e.Records(config).directory(call);accepted=c.read(directory/'accepted.json')
    source=c.workspace(root,2)/'code/second';source.write_bytes(b'no second submission')
    for state in ('accepted','submitted','queued','running','cancelling','unknown','unrecognized'):
        path=directory/'backend-accepted.json';backend=c.read(path);backend['state']=state;c.write(path,backend)
        before={p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}
        expected='unknown' if state in {'unknown','unrecognized'} else state
        result=e.dispatch(config,source,new=True,mechanism='2')
        assert result['call_id']==call and result['status']==expected
        progress=e.progress(config)
        assert progress['counts'][expected]==1 and progress['calls'][0]['status']==expected
        if state in {'queued','cancelling'}:
            assert cli('run','--payload',source,'--new-plan','--mechanism','2',expected=2)['status']==state
            assert cli('reconcile','--call-id',call,expected=2)['status']==state
        assert accepted==c.read(directory/'accepted.json') and len(e.Records(config).calls())==1
        assert before=={p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    # A pending string without the original external binding is still unknown.
    backend['state']='queued';c.write(path,backend);(directory/'submission.json').unlink()
    assert e.dispatch(config,source,new=True,mechanism='2')['status']=='unknown'
    assert cli('reconcile','--call-id',call,expected=2)['status']=='unknown'
    assert len(e.Records(config).calls())==1
