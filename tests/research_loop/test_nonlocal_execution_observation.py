"""Backend terminal facts reach scientific seals without invented local processes."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from execution_fixtures import execution_registry, remote_registry
from test_history_handoff import finish
from scidiscovery.research_loop import (accepted_budget, budget, contracts as c, controller, execution, experiments as e,
    history, search, worker_delivery)


def case(tmp_path, monkeypatch, outcome='known_success', executor='remote_execution:fixture', extra=None, resume=False):
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md'
    objective.write_text('Synthetic execution contract; scientific adequacy remains for A.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable] if resume else ['never-launched'],max_tokens=10000,max_seconds=120,max_steps=10,
        model='no-model',reasoning_effort='high',node_budget_mode='soft',experiment_mode='external',
        **(dict(resume_policy=dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn'),
            node_budget_policy={role:dict(max_observed_tokens=1000,max_seconds=30) for role in 'HWAE'}) if resume else {}))
    finish(root,1,'W',[],stage_intent='handoff',
        text='A bounded synthetic discovery; no scientific qualification is asserted.')
    job=c.build_job(root,2,'W',[1]);record=execution.record(root,2)
    node_anchor=budget.accept_node(root,job)
    anchor=dict(budget_slice=node_anchor) if node_anchor is not None else dict(search_slice=search.accept(root,job))
    from scidiscovery.research_loop import accepted_budget
    accepted_budget.accept_native(root,2,record,dict(task_ref=c.record_ref(root/'jobs/002.json'),**anchor))
    config_path=c.workspace(root,2)/'work/experiment-config.json'
    options=dict(outcome=outcome) if executor.startswith('remote_') else dict(run_argv=[sys.executable])
    options.update(extra or {})
    config=e.configure(root,2,executor,options,config_path)
    (c.workspace(root,2)/'code').mkdir(exist_ok=True)
    if os.environ.get('SCID_NONLOCAL_INSTALLED')!='1':
        monkeypatch.setenv('PYTHONPATH',str(Path(e.__file__).parents[2])+os.pathsep+os.environ.get('PYTHONPATH',''))
    def cli(*args, expected=0, config_file=config_path):
        isolated=['-I'] if os.environ.get('SCID_NONLOCAL_INSTALLED')=='1' else []
        reply=subprocess.run([sys.executable,*isolated,'-m','scidiscovery.research_loop.experiments',
            '--config',str(config_file),*map(str,args)],capture_output=True,text=True)
        assert reply.returncode==expected,(reply.stdout,reply.stderr)
        return json.loads(reply.stdout)
    return root,job,config,cli


def research(root,number):
    """An authored current W batch, separate from public mechanism numbers."""
    assert isinstance(number,str) and number.isdecimal() and int(number)>0
    ref='w002.m'+number
    return dict(declarations=[dict(mechanism_ref=ref,action='declare',
        question='Compare synthetic mechanism '+number,exploration=False)],mechanism_ref=ref,
        experiment_question=dict(question='Read the exact synthetic observation for mechanism '+number,
            change_criterion='A changed terminal observation changes the next bounded decision'))


def research_path(root,number):
    path=c.workspace(root,2)/'work'/('research-'+number+'.json')
    c.write(path,research(root,number))
    return str(path)


def run(root,cli,label,outcome='known_success'):
    payload=c.workspace(root,2)/'code'/('opaque-'+label)
    payload.parent.mkdir(exist_ok=True)
    payload.write_bytes(b'opaque remote package; never interpreted as local Python')
    return cli('run','--payload',payload,'--new-plan','--research',research_path(root,label),
        expected={'known_success':0,'known_failed':1,'unknown':2}[outcome])


def observe_calls(root,job,config):
    """Produce current call facts for case's already accepted original attempt."""
    return history._observe_managed_calls_from_facts(root,job,
        chain=[execution.origin(root,job['step'])],scope=config['scope'])


@pytest.mark.process_e2e
@pytest.mark.parametrize('outcome,state',[('known_success','succeeded'),('known_failed','failed')])
def test_remote_cli_terminal_collect_and_seal_exact_originals(tmp_path,remote_registry,monkeypatch,outcome,state):
    root,job,config,cli=case(tmp_path,monkeypatch,outcome)
    results=[run(root,cli,ref,outcome) for ref in ('1','2')]
    for result in results:
        directory=e.Records(config).directory(result['call_id'])
        assert not (directory/'process.json').exists() and not (directory/'terminal.json').exists()
        assert cli('query','--call-id',result['call_id'])['state']==state
        assert cli('lookup','--call-id',result['call_id'])['state']==state
        collected=cli('collect','--call-id',result['call_id'])
        assert collected['status']=='collected' and len(collected['outputs'])==1
    findings=[dict(mechanism=label,basis='observed',finding='Synthetic terminal observation; science remains unqualified.') for label in ('w002.m1','w002.m2')]
    finish(root,2,'W',[1],mechanism_findings=findings,preserve_native_intent=True)
    record=execution.record(root,2);seal=c.read(record/'result-identity.json')
    facts=c.read(root/seal['observations']['path'])
    assert [row['status'] for row in facts['managed_calls']]==[outcome,outcome]
    assert seal['control_outcome']['worker_delivery']['status']=='not_applicable'
    # Distinct calls retain distinct originals even when their output bytes agree.
    assert len(facts['managed_calls'])==2
    assert len({row['call_id'] for row in facts['managed_calls']})==2
    outputs=[row['outputs'][0] for row in facts['managed_calls']]
    assert len(seal['materials'])==2
    assert {row['path'].split('/artifacts/',1)[1] for row in seal['materials']}=={
        'experiments/'+row['call_id']+'/artifacts/original' for row in facts['managed_calls']}
    assert {str(root/row['snapshot']) for row in outputs}=={row['source'] for row in seal['materials']}
    for row in facts['managed_calls']:
        assert row['terminal'] is None and row['observation']['retention']=='archived'
        assert len(row['outputs'])==1 and row['outputs'][0]['retention']=='archived'
        assert (root/row['outputs'][0]['snapshot']).read_bytes()==b'opaque remote package; never interpreted as local Python'
        assert all(item['retention']=='archived' for item in row['originals'])
    assert c.validate(c.workspace(root,2))['scientific_result']==c.read(record/'science-output.json')['scientific_result']


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault',['missing-observation','accepted','completed','external-id','native-proof','collected'])
def test_remote_terminal_missing_or_tampered_originals_never_become_known(tmp_path,remote_registry,monkeypatch,fault):
    root,job,config,cli=case(tmp_path,monkeypatch)
    result=run(root,cli,'1');directory=e.Records(config).directory(result['call_id'])
    if fault=='missing-observation':
        (directory/'execution-observation.json').unlink()
        rows=observe_calls(root,job,config)
        assert rows[0]['status']=='unknown'
        originals=[directory/name for name in ('accepted.json','completed.json','submission.json','remote-terminal.json')]
        before={path:path.read_bytes() for path in originals if path.is_file()}
        assert cli('query','--call-id',result['call_id'])['state']=='succeeded'
        assert all(path.read_bytes()==raw for path,raw in before.items())
        assert not (directory/'execution-observation.json').exists()
        return
    target={'accepted':'accepted.json','completed':'completed.json','external-id':'submission.json','native-proof':'remote-terminal.json'}
    if fault in {'collected','native-proof'}:
        observed=c.read(directory/'execution-observation.json')
        proof=observed['outputs'][0] if fault=='collected' else observed['proofs'][-1]
        Path(proof['path']).chmod(0o600);Path(proof['path']).write_bytes(b'changed retained collection or native proof')
    else:
        path=directory/target[fault];value=c.read(path)
        if fault=='external-id':value['external_run_id']='remote-other'
        elif fault=='completed':value['status']='known_failed'
        elif fault=='accepted':value['interface']=dict(value['interface'],executor='other:binding')
        else:value['scope']=dict(value['scope'],execution_key='f'*64)
        c.write(path,value)
    with pytest.raises((ValueError,OSError)):
        observe_calls(root,job,config)


@pytest.mark.process_e2e
def test_remote_unknown_is_preserved_without_replay_or_worker_qualification(tmp_path,remote_registry,monkeypatch):
    root,job,config,cli=case(tmp_path,monkeypatch,'unknown')
    result=run(root,cli,'1','unknown')
    directory=e.Records(config).directory(result['call_id'])
    assert not (directory/'execution-observation.json').exists()
    assert observe_calls(root,job,config)[0]['status']=='unknown'
    package=c.workspace(root,2)/'code/another';package.write_bytes(b'never submitted')
    assert e.dispatch(config,package,new=True,mechanism=research(root,'2'))['status']=='unknown'
    assert len(e.Records(config).calls())==1
    accepted=c.read(directory/'accepted.json');completed=c.read(directory/'completed.json')
    assert not worker_delivery._candidate(root,accepted,completed,None)




@pytest.mark.process_e2e
@pytest.mark.parametrize('extra',[dict(collect_name='artifact/../../escape'),dict(collect_name='artifact/'),dict(duplicate_collection=True)])
def test_remote_collection_names_cannot_escape_or_alias_deliverable_paths(tmp_path,remote_registry,monkeypatch,extra):
    root,job,config,cli=case(tmp_path,monkeypatch,extra=extra)
    package=c.workspace(root,2)/'code/package';package.write_bytes(b'opaque input')
    reply=cli('run','--payload',package,'--new-plan','--research',research_path(root,'1'),expected=3)
    assert reply['status']=='integrity_error'
    assert observe_calls(root,job,config)[0]['status']=='unknown'
    assert not (root/'escape').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('include_result',[False,True])
def test_sealed_remote_output_is_retained_but_active_validation_rejects_missing_original(tmp_path,remote_registry,monkeypatch,include_result):
    outside=tmp_path/'backend-state'
    root,job,config,cli=case(tmp_path,monkeypatch,extra=dict(proof_directory=str(outside),include_result=include_result))
    result=run(root,cli,'1');native_proof=outside/(result['call_id']+'.json')
    assert native_proof.is_file() and not native_proof.is_relative_to(root)
    monkeypatch.setattr(e,'load',lambda *a,**kw:pytest.fail('scientific sealing loaded an adapter'))
    # First acceptance requires the exact external original to remain readable.
    finish(root,2,'W',[1],preserve_native_intent=True,mechanism_findings=[
        dict(mechanism='w002.m1',basis='observed',finding='Retained native proof; no scientific qualification.')])
    record=execution.record(root,2);seal_path=record/'result-identity.json'
    sealed_bytes=seal_path.read_bytes();seal=c.read(seal_path)
    fact_path=root/seal['observations']['path'];fact_bytes=fact_path.read_bytes();fact=c.read(fact_path)
    assert fact['managed_calls'][0]['status']=='known_success'
    c.validate(c.workspace(root,2))
    native_proof.unlink()
    # Active workspace validation still requires its exact native original;
    # retained output snapshots do not substitute for that validation input.
    with pytest.raises(ValueError,match='Expected an original regular file'):
        c.validate(c.workspace(root,2))
    assert seal_path.read_bytes()==sealed_bytes and fact_path.read_bytes()==fact_bytes
    assert not native_proof.exists() and len(e.Records(config).calls())==1
    output=fact['managed_calls'][0]['outputs'][0]
    assert (root/output['snapshot']).read_bytes()==b'opaque remote package; never interpreted as local Python'


@pytest.mark.process_e2e
def test_managed_outputs_first_configured_on_later_attempt_use_that_host_interface(tmp_path,remote_registry,monkeypatch):
    root,job,config,cli=case(tmp_path,monkeypatch,resume=True)
    origin=execution.origin(root,2)
    (origin/'experiment-config.json').unlink();(c.workspace(root,2)/'work/experiment-config.json').unlink()
    meter=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3)
    c.write(origin/'usage.json',meter)
    report=dict(budget.node_report(root,job,c.read(origin/'native-intent.json'),meter),usage_observed=True)
    c.write(origin/'native-outcome.json',dict(returncode=1,stop_reason='synthetic original stopped',budget_slice=report))
    accepted_budget.accept_terminal(root,2,origin,meter=meter,unknown=False)
    anchor=budget.accept_continuation(root,job,[origin])
    assert anchor['previous_tokens']==10
    later=origin/'continuations/001';later.mkdir(parents=True)
    c.write(later/'native-intent.json',dict(attempt=1,task_ref=c.record_ref(root/'jobs/002.json'),
        previous=execution.proof(origin),root_intent_ref=c.record_ref(origin/'native-intent.json'),budget_slice=anchor))
    accepted_budget.accept_native(root,2,later,c.read(later/'native-intent.json'))
    path=c.workspace(root,2)/'work/later-config.json'
    current=e.configure(root,2,'remote_execution:fixture',{},path)
    package=c.workspace(root,2)/'code/later-package';package.write_bytes(b'later attempt opaque input')
    result=e.dispatch(current,package,new=True,mechanism=research(root,'1'))
    accepted=e.Records(current).accepted(result['call_id'])
    assert accepted['native_attempt']['number']==1 and not (origin/'experiment-config.json').exists()
    chain=[origin,later]
    assert history._execution_interface_from_chain(root,accepted,chain)==current['execution_interface']
    rows=history._observe_managed_calls_from_facts(root,job,chain=chain,scope=current['scope'])
    assert len(rows)==1 and rows[0]['status']=='known_success'
    assert (root/rows[0]['outputs'][0]['snapshot']).read_bytes()==package.read_bytes()
    wrong=copy.deepcopy(accepted);wrong['native_attempt']['number']=0
    with pytest.raises(history.ObserverIntegrityError,match='configuration missing'):
        history._execution_interface_from_chain(root,wrong,chain)
    meter=dict(input_tokens=5,cached_input_tokens=1,output_tokens=2)
    c.write(later/'usage.json',meter)
    report=dict(budget.node_report(root,job,c.read(later/'native-intent.json'),meter),usage_observed=True)
    c.write(later/'native-outcome.json',dict(returncode=1,stop_reason='synthetic bounded handoff',budget_slice=report))
    head=accepted_budget.accept_terminal(root,2,later,meter=meter,unknown=False)
    assert head.current_job['reported_tokens']==17 and head['current_native'] is None
    assert len(e.Records(current).calls())==1
