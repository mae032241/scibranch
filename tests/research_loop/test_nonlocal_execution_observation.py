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
from scidiscovery.research_loop import (budget, checkpoint, contracts as c, controller, execution, experiments as e,
    history, native, qualification as q, search, worker_delivery)


def case(tmp_path, monkeypatch, outcome='known_success', executor='remote_execution:fixture', extra=None, resume=False):
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md'
    objective.write_text('Synthetic execution contract; scientific adequacy remains for A.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable] if resume else ['never-launched'],max_tokens=10000,max_seconds=120,max_steps=10,
        model='no-model',reasoning_effort='high',node_budget_mode='soft',experiment_mode='external',
        **(dict(resume_policy=dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn'),
            node_budget_policy={role:dict(max_observed_tokens=1000,max_seconds=30) for role in 'HWAE'}) if resume else {}))
    finish(root,1,'H',[],action='experiment',search_choice=dict(scope='Two explicit synthetic mechanisms',
        commitments=[dict(label=label,minimal_test='Compare the declared synthetic observation',existing_ref=None)
            for label in ('P','Q')],focus=dict(index=None,action='screen')))
    job=c.build_job(root,2,'W',[1]);record=execution.record(root,2)
    node_anchor=budget.accept_node(root,job)
    anchor=dict(budget_slice=node_anchor) if node_anchor is not None else dict(search_slice=search.accept(root,job))
    from scidiscovery.research_loop import accepted_budget
    accepted_budget.accept_native(root,2,record,dict(task_ref=c.record_ref(root/'jobs/002.json'),**anchor))
    config_path=c.workspace(root,2)/'work/experiment-config.json'
    options=dict(outcome=outcome) if executor.startswith('remote_') else dict(run_argv=[sys.executable])
    options.update(extra or {})
    config=e.configure(root,2,executor,options,config_path)
    if os.environ.get('SCID_NONLOCAL_INSTALLED')!='1':
        monkeypatch.setenv('PYTHONPATH',str(Path(e.__file__).parents[2])+os.pathsep+os.environ.get('PYTHONPATH',''))
    def cli(*args, expected=0, config_file=config_path):
        isolated=['-I'] if os.environ.get('SCID_NONLOCAL_INSTALLED')=='1' else []
        reply=subprocess.run([sys.executable,*isolated,'-m','scidiscovery.research_loop.experiments',
            '--config',str(config_file),*map(str,args)],capture_output=True,text=True)
        assert reply.returncode==expected,(reply.stdout,reply.stderr)
        return json.loads(reply.stdout)
    return root,job,config,cli


def run(root,cli,label,outcome='known_success'):
    payload=c.workspace(root,2)/'work'/('opaque-'+label)
    payload.write_bytes(b'opaque remote package; never interpreted as local Python')
    result=cli('run','--payload',payload,'--new-plan','--mechanism',label,
        expected={'known_success':0,'known_failed':1,'unknown':2}[outcome])
    return result


@pytest.mark.process_e2e
@pytest.mark.parametrize('outcome,state',[('known_success','succeeded'),('known_failed','failed')])
def test_remote_cli_terminal_collect_history_worker_a_and_successor_admission(tmp_path,remote_registry,monkeypatch,outcome,state):
    root,job,config,cli=case(tmp_path,monkeypatch,outcome)
    results=[run(root,cli,ref,outcome) for ref in ('h001.m1','h001.m2')]
    for result in results:
        directory=e.Records(config).directory(result['call_id'])
        assert not (directory/'process.json').exists() and not (directory/'terminal.json').exists()
        assert cli('query','--call-id',result['call_id'])['state']==state
        assert cli('lookup','--call-id',result['call_id'])['state']==state
        collected=cli('collect','--call-id',result['call_id'])
        assert collected['status']=='collected' and len(collected['outputs'])==1
    findings=[dict(mechanism=label,basis='observed',finding='Synthetic terminal observation; science remains unqualified.') for label in ('h001.m1','h001.m2')]
    finish(root,2,'W',[1],mechanism_findings=findings,preserve_native_intent=True)
    record=execution.record(root,2);seal=c.read(record/'result-identity.json')
    facts=c.read(root/seal['observations']['path'])
    assert [row['status'] for row in facts['managed_calls']]==[outcome,outcome]
    assert seal['control_outcome']['worker_delivery']['status']=='complete'
    # Identical bytes from distinct calls retain both scientific identities.
    expected_names={f'experiments/{result["call_id"]}/artifacts/original' for result in results}
    saved=checkpoint.validate(root,job,record)
    assert len(saved['materials'])==len(expected_names)==2
    assert {row['name'] for row in saved['materials']}==expected_names
    outputs={f'experiments/{call["call_id"]}/artifacts/original':call['outputs'][0]
        for call in facts['managed_calls']}
    assert len({(root/output['snapshot']).read_bytes() for output in outputs.values()})==1
    for row in saved['materials']:
        output=outputs[row['name']]
        assert row['original']['retention']=='archived'
        assert all(row['original'][key]==output[key] for key in ('snapshot','size_bytes'))
        assert (root/row['original']['snapshot']).read_bytes()==b'opaque remote package; never interpreted as local Python'
    preview=checkpoint.brief(root,job,record)
    assert {row['name'] for row in preview['direct_files']}==expected_names and preview['omitted_files']==0
    assert all(set(row)=={'name','name_truncated','retention'} and not row['name_truncated']
        and row['retention']=='archived' for row in preview['direct_files'])
    assert 'host-history' not in json.dumps(preview) and '/objects/' not in json.dumps(preview)
    assert str(root) not in json.dumps(preview)
    for row in facts['managed_calls']:
        assert row['terminal'] is None and row['observation']['retention']=='archived'
        assert len(row['outputs'])==1 and row['outputs'][0]['retention']=='archived'
        assert (root/row['outputs'][0]['snapshot']).read_bytes().startswith(b'opaque remote package')
        assert all(item['retention']=='archived' for item in row['originals'])
    a_job=c.build_job(root,3,'A',[1,2]);a_record=execution.record(root,3)
    materials=a_job['direct_materials']
    assert len(materials)==2 and materials[0]['source']!=materials[1]['source']
    assert materials[0]['path']!=materials[1]['path']
    assert not any(edge['kind']=='unknown' for edge in q._inputs(root,a_job)['edges'])
    unchanged_job=copy.deepcopy(a_job)
    before_negative={path:path.read_bytes() for path in root.rglob('*') if path.is_file()}
    for key,value in (('size_bytes',materials[0]['size_bytes']+1),
                      ('path',materials[0]['path']+'/forged')):
        forged=copy.deepcopy(a_job)
        forged['direct_materials'][0][key]=value
        assert forged['direct_materials'][0]['source']==materials[0]['source']
        with pytest.raises(q.IntegrityError,match='Bound material differs from its exact producer original'):
            q._inputs(root,forged)
    assert a_job==unchanged_job
    assert before_negative=={path:path.read_bytes() for path in root.rglob('*') if path.is_file()}
    page=a_job['history']['view']['indexes']['experiments'];mounted=[]
    while page:
        index=c.read(c.workspace(root,3)/page)
        for call in index['experiments']:
            for item in call['outputs']:
                mounted.append((c.workspace(root,3)/item['path']).read_bytes())
        page=index['next_page']
    assert len(mounted)==2 and all(data.startswith(b'opaque remote package') for data in mounted)
    payload=dict(summary='Interpret exact remote observations',scientific_result='Fixture observations retain their limitations.',
        limitations=['No model or solver ran.'],implementation_selection=None,candidate_recommendation=None,
        progress_assessment=None,portfolio_review=None,search_review=dict(
            dispositions=[dict(mechanism_ref='h001.m1',status='screened',reason='A assesses the exact final W observation')],
            next_focus=dict(mechanism_ref='h001.m1',action='improve')),
        next_action='continue_w',route_reason='Improve the chosen synthetic mechanism',
        decision_request=dict(decision='mechanism',question='Which observation changes the mechanism choice?',
            change_criterion='A new discriminating observation changes the choice'))
    c.write(a_record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/003.json')))
    c.write(a_record/'science-output.json',payload);c.write(a_record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    c.write(a_record/'native-outcome.json',dict(returncode=0,stop_reason=None,usage=native.usage(root),raw_ref=c.file_ref(a_record/'science-output.json')))
    c.seal_result(root,3)
    assert search.project(root,4)['mechanisms']['h001.m1']['status']=='screened'
    successor=c.build_job(root,4,'W',[1,2,3]);next_record=execution.record(root,4)
    node_anchor=budget.accept_node(root,successor)
    anchor=dict(budget_slice=node_anchor) if node_anchor is not None else dict(search_slice=search.accept(root,successor))
    c.write(next_record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/004.json'),**anchor))
    next_config=e.configure(root,4,'remote_execution:fixture',{},c.workspace(root,4)/'work/experiment-config.json')
    assert not next_config['breadth']['prior_unknown']
    package=c.workspace(root,4)/'work/new-package';package.write_bytes(b'new authorized opaque input')
    assert e.dispatch(next_config,package,new=True,mechanism='h001.m1')['status']=='known_success'
    # Sealed readers never load or query the plugin, and do not reconstruct facts.
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    monkeypatch.setattr(e,'load',lambda *a,**kw:pytest.fail('sealed validation queried an adapter'))
    c.validate(c.workspace(root,2));c.validate(c.workspace(root,3));history.validate_managed(root,job,facts['managed_calls'])
    assert before=={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault',['missing-observation','accepted','completed','external-id','native-proof','collected'])
def test_remote_terminal_missing_or_tampered_originals_never_become_known(tmp_path,remote_registry,monkeypatch,fault):
    root,job,config,cli=case(tmp_path,monkeypatch)
    result=run(root,cli,'h001.m1');directory=e.Records(config).directory(result['call_id'])
    if fault=='missing-observation':
        (directory/'execution-observation.json').unlink()
        rows=history.managed_calls(root,job)
        assert rows[0]['status']=='unknown'
        before={p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}
        assert cli('query','--call-id',result['call_id'])['state']=='succeeded'
        assert before=={p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}
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
        history.managed_calls(root,job)


@pytest.mark.process_e2e
def test_remote_unknown_is_preserved_without_replay_or_worker_qualification(tmp_path,remote_registry,monkeypatch):
    root,job,config,cli=case(tmp_path,monkeypatch,'unknown')
    result=run(root,cli,'h001.m1','unknown')
    directory=e.Records(config).directory(result['call_id'])
    assert not (directory/'execution-observation.json').exists()
    assert history.managed_calls(root,job)[0]['status']=='unknown'
    package=c.workspace(root,2)/'work/another';package.write_bytes(b'never submitted')
    assert e.dispatch(config,package,new=True,mechanism='h001.m2')['status']=='unknown'
    assert len(e.Records(config).calls())==1
    accepted=c.read(directory/'accepted.json');completed=c.read(directory/'completed.json')
    assert not worker_delivery._candidate(root,accepted,completed,None)


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault',['cleanup','transport','returncode','status','masquerade'])
def test_local_control_observation_keeps_local_terminal_guarantees(tmp_path,execution_registry,monkeypatch,fault):
    root,job,config,cli=case(tmp_path,monkeypatch,executor='local_execution:command')
    source=c.workspace(root,2)/'work/local.py';source.write_text('print("local original")\n')
    result=e.dispatch(config,source,new=True,mechanism='h001.m1');directory=e.Records(config).directory(result['call_id'])
    observed=c.read(directory/'execution-observation.json')
    if fault=='masquerade':
        observed['interface']=dict(observed['interface'],terminal_evidence='adapter_result')
        observed.pop('native_terminal')
    else:
        terminal=c.read(directory/'terminal.json')
        if fault=='cleanup':terminal['cleanup_confirmed']=False
        elif fault=='transport':terminal['transport_complete']=False
        elif fault=='returncode':terminal['returncode']=None
        else:terminal['returncode']=9
        c.write(directory/'terminal.json',terminal)
        observed['native_terminal']=e.proof(directory/'terminal.json')
    with pytest.raises(ValueError):
        e.verify_observation(observed,lambda item:e.read(item['path'],e.MAX_ASSET),interface=config['execution_interface'])


@pytest.mark.process_e2e
def test_legacy_local_receipts_remain_readable_without_creating_control_facts(tmp_path,execution_registry,monkeypatch):
    root,job,config,cli=case(tmp_path,monkeypatch,executor='local_execution:command')
    source=c.workspace(root,2)/'work/local.py';source.write_text('pass\n')
    result=e.dispatch(config,source,new=True,mechanism='h001.m1');directory=e.Records(config).directory(result['call_id'])
    accepted=c.read(directory/'accepted.json');old=copy.deepcopy(config['execution_interface'])
    old.pop('observation_version');old.pop('terminal_evidence');accepted['interface']=old
    c.write(directory/'accepted.json',accepted)
    host_path=execution.record(root,2)/'experiment-config.json';host=c.read(host_path);host['execution_interface']=old;c.write(host_path,host)
    completed=c.read(directory/'completed.json')
    completed['proofs']=[e.proof(item['path']) for item in completed['proofs']];c.write(directory/'completed.json',completed)
    (directory/'execution-observation.json').unlink()
    rows=history.managed_calls(root,job);history.validate_managed(root,job,rows)
    assert rows[0]['status']=='known_success' and 'observation' not in rows[0]
    assert not (directory/'execution-observation.json').exists()


@pytest.mark.process_e2e
@pytest.mark.skipif(os.environ.get('SCID_NONLOCAL_INSTALLED')=='1',reason='Legacy cov fixture runs in the source compatibility lane')
def test_legacy_cov_receipts_keep_original_terminal_authority(tmp_path,execution_registry):
    from test_breadth_science import prepare,h,worker,run
    root=prepare(tmp_path,cov=True);h(root,labels=('P',))
    job,config=worker(root,tmp_path,2,[1],cov=True)
    result=run(root,config,'h001.m1');directory=e.Records(config).directory(result['call_id'])
    accepted=c.read(directory/'accepted.json');old=copy.deepcopy(config['execution_interface'])
    old.pop('observation_version');old.pop('terminal_evidence');accepted['interface']=old
    c.write(directory/'accepted.json',accepted)
    host_path=execution.record(root,2)/'experiment-config.json';host=c.read(host_path);host['execution_interface']=old;c.write(host_path,host)
    completed=c.read(directory/'completed.json');completed['proofs']=[e.proof(item['path']) for item in completed['proofs']]
    c.write(directory/'completed.json',completed);(directory/'execution-observation.json').unlink()
    rows=history.managed_calls(root,job);history.validate_managed(root,job,rows)
    assert rows[0]['status']=='known_success' and 'observation' not in rows[0]


@pytest.mark.process_e2e
@pytest.mark.parametrize('extra',[dict(collect_name='artifact/../../escape'),dict(collect_name='artifact/'),dict(duplicate_collection=True)])
def test_remote_collection_names_cannot_escape_or_alias_deliverable_paths(tmp_path,remote_registry,monkeypatch,extra):
    root,job,config,cli=case(tmp_path,monkeypatch,extra=extra)
    package=c.workspace(root,2)/'work/package';package.write_bytes(b'opaque input')
    reply=cli('run','--payload',package,'--new-plan','--mechanism','h001.m1',expected=3)
    assert reply['status']=='integrity_error'
    assert history.managed_calls(root,job)[0]['status']=='unknown'
    assert c.collect_materials(root,2)==[]
    assert not (root/'escape').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('include_result',[False,True])
def test_retained_remote_proof_outside_instance_survives_native_source_removal(tmp_path,remote_registry,monkeypatch,include_result):
    outside=tmp_path/'backend-state'
    root,job,config,cli=case(tmp_path,monkeypatch,extra=dict(proof_directory=str(outside),include_result=include_result))
    result=run(root,cli,'h001.m1');native_proof=outside/(result['call_id']+'.json')
    assert native_proof.is_file() and not native_proof.is_relative_to(root)
    native_proof.unlink()
    monkeypatch.setattr(e,'load',lambda *a,**kw:pytest.fail('scientific sealing loaded an adapter'))
    finish(root,2,'W',[1],mechanism_findings=[
        dict(mechanism='h001.m1',basis='observed',finding='Retained native proof; no scientific qualification.'),
        dict(mechanism='h001.m2',basis='blocked',finding='Not executed in this bounded fixture.')])
    seal=c.read(execution.record(root,2)/'result-identity.json');fact=c.read(root/seal['observations']['path'])
    assert fact['managed_calls'][0]['status']=='known_success'
    history.validate_managed(root,job,fact['managed_calls']);c.validate(c.workspace(root,2))
    a=c.build_job(root,3,'A',[1,2]);index=c.read(c.workspace(root,3)/a['history']['view']['indexes']['experiments'])
    path=index['experiments'][0]['outputs'][0]['path']
    assert (c.workspace(root,3)/path).read_bytes().startswith(b'opaque remote package')


def test_managed_outputs_first_configured_on_later_attempt_use_that_host_interface(tmp_path,remote_registry,monkeypatch):
    root,job,config,cli=case(tmp_path,monkeypatch,resume=True)
    origin=execution.origin(root,2)
    (origin/'experiment-config.json').unlink();(c.workspace(root,2)/'work/experiment-config.json').unlink()
    meter=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3)
    c.write(origin/'usage.json',meter)
    report=dict(budget.node_report(root,job,c.read(origin/'native-intent.json'),meter),usage_observed=True)
    c.write(origin/'native-outcome.json',dict(returncode=1,stop_reason='synthetic original stopped',budget_slice=report))
    anchor=budget.accept_continuation(root,job,[origin])
    assert anchor['previous_tokens']==10
    later=origin/'continuations/001';later.mkdir(parents=True)
    c.write(later/'native-intent.json',dict(attempt=1,task_ref=c.record_ref(root/'jobs/002.json'),
        previous=execution.proof(origin),root_intent_ref=c.record_ref(origin/'native-intent.json'),budget_slice=anchor))
    path=c.workspace(root,2)/'work/later-config.json'
    current=e.configure(root,2,'remote_execution:fixture',{},path)
    package=c.workspace(root,2)/'work/later-package';package.write_bytes(b'later attempt opaque input')
    result=e.dispatch(current,package,new=True,mechanism='h001.m1')
    accepted=e.Records(current).accepted(result['call_id'])
    assert accepted['native_attempt']['number']==1 and not (origin/'experiment-config.json').exists()
    assert history._execution_interface(root,accepted)==current['execution_interface']
    materials=c.collect_materials(root,2)
    assert len(materials)==1 and Path(materials[0]['source']).read_bytes()==package.read_bytes()
    wrong=copy.deepcopy(accepted);wrong['native_attempt']['number']=0
    with pytest.raises(history.ObserverIntegrityError,match='configuration missing'):
        history._execution_interface(root,wrong)
    meter=dict(input_tokens=5,cached_input_tokens=1,output_tokens=2)
    c.write(later/'usage.json',meter)
    report=dict(budget.node_report(root,job,c.read(later/'native-intent.json'),meter),usage_observed=True)
    c.write(later/'native-outcome.json',dict(returncode=1,stop_reason='synthetic bounded handoff',budget_slice=report))
    anchor=budget.accept_continuation(root,job,[origin,later])
    assert anchor['previous_tokens']==17
    last=origin/'continuations/002';last.mkdir()
    c.write(last/'native-intent.json',dict(attempt=2,task_ref=c.record_ref(root/'jobs/002.json'),
        previous=execution.proof(later),root_intent_ref=c.record_ref(origin/'native-intent.json'),budget_slice=anchor))
    assert execution.record(root,2)==last and not (last/'experiment-config.json').exists()
    assert c.collect_materials(root,2)==materials  # A later native closeout need not rerun the executor.
