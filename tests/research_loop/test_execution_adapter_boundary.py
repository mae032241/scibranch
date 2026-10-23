"""Non-domain adapter contracts; no model, solver or external transport."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest

from execution_fixtures import DifferentExternalIdentity, execution_registry, remote_registry
from scidiscovery.operations.execution import ExecutionInterfaceRegistration
from scidiscovery.operations.runtime_plugins import RuntimePluginFactory
from scidiscovery.research_loop import (author_closeout, candidate_audit, contracts as c,
    experiments as e, generic_results, history, lineage, protocol, ui_view, managed_wait)


def scope():
    return dict(handoff_protocol=protocol.CURRENT,step=2,role='W',job_ref=dict(path='/fixture/jobs/002.json'),
        execution_key=dict(path='/fixture/native-evidence/002/native-intent.json'),mechanism_ref='h001.m1')


def test_new_terminal_freezes_public_fields_and_same_named_native_json(tmp_path,remote_registry,monkeypatch,capsys):
    monkeypatch.setattr(DifferentExternalIdentity,'remote_path',lambda self,call:self.records.directory(call)/'row.json')
    public_calls=[]
    def public(self,result):
        public_calls.append(result['call_id'])
        return dict(status=result['status'],call_id=result['call_id'],evaluation_key=None,projection_number=len(public_calls),
            observations=dict(arbitrary_metric=0.75,replicates=[0.7,0.8]))
    monkeypatch.setattr(DifferentExternalIdentity,'public_result',public)
    import execution_fixtures
    original_collect=DifferentExternalIdentity.collect_original
    original_result=DifferentExternalIdentity.result
    def result(self,call):
        if (self.records.directory(call)/'execution-observation.json').exists():
            raise AssertionError('A terminal must use its frozen completed original')
        return original_result(self,call)
    monkeypatch.setattr(DifferentExternalIdentity,'result',result)
    def collect(self,call):
        descriptors=original_collect(self,call)
        private=self.records.directory(call)/'host-private';private.write_bytes(b'PRIVATE-NATIVE-SECRET')
        return (*descriptors,e.LocalFileDescriptor(name='private',local_path=str(private),
            size_bytes=private.stat().st_size,media_type='application/octet-stream'))
    monkeypatch.setattr(DifferentExternalIdentity,'collect_original',collect)
    def selected(descriptors,*,projection_directory):
        return tuple(item for item in descriptors if item.name=='original')
    monkeypatch.setattr(execution_fixtures,'REMOTE_REGISTRATION',replace(execution_fixtures.REMOTE_REGISTRATION,
        scientific_outputs=selected,projection_contract='laboratory.public-projections.v1'))
    config=e.freeze(dict(record_dir=str(tmp_path/'records'),scope=scope(),admission=dict(version=1,mode='hard')),
        'remote_execution:fixture')
    payload=tmp_path/'payload';payload.write_bytes(b'opaque input')
    config_path=tmp_path/'config.json';c.write(config_path,config)
    assert e.main(['--config',str(config_path),'run','--payload',str(payload)])==0
    reply=json.loads(capsys.readouterr().out)
    directory=e.Records(config).directory(reply['call_id'])
    observed=c.read(directory/'execution-observation.json')
    completed=c.read(Path(observed['completed']['path']))
    expected=observed['public_result']
    assert all(reply.get(key)==value for key,value in expected.items())
    assert len(public_calls)==1
    assert {'engineering_check','feedback','handoff'} <= set(reply)
    assert reply['engineering_check']['status']=='unsupported'
    assert e.scientific_result(config,c.read(directory/'accepted.json'),completed,observed)==expected
    assert e._run_view(expected)==expected
    assert [item['name'] for item in observed['scientific_outputs']]==['original']
    assert len(observed['outputs'])==2
    assert 'PRIVATE-NATIVE-SECRET' not in json.dumps(reply)
    # The real wake reader and history capture use the same retained value.
    work=tmp_path/'wake';work.mkdir()
    monkeypatch.setattr(c,'workspace',lambda root,step:work)
    monkeypatch.setattr(managed_wait,'_config',lambda root,job,accepted:(None,config))
    managed_wait.wake_packet(tmp_path,dict(step=2,worker_view_version=1),dict(execution_wake=dict(
        observations=[e.proof(e.Records(config).directory(reply['call_id'])/'execution-observation.json')])))
    assert c.read(work/'work/execution-results/001/result.json')==expected
    from scidiscovery.research_loop import experiment_client
    monkeypatch.setattr(experiment_client,'record_directory',lambda root,step:Path(config['record_dir']))
    monkeypatch.setattr(protocol,'stage_scope',lambda root,job:scope())
    monkeypatch.setattr(protocol,'require_current',lambda *args,**kwargs:None)
    monkeypatch.setattr(history,'_execution_interface',lambda root,accepted:config['execution_interface'])
    archived_calls=history.managed_calls(tmp_path,dict(step=2))
    history.validate_managed(tmp_path,dict(step=2),archived_calls)
    assert archived_calls[-1]['execution']['public_result']==expected
    assert [item['name'] for item in archived_calls[-1]['outputs']]==['original']
    def unavailable(*args,**kwargs):raise AssertionError('A frozen terminal must not load a provider')
    monkeypatch.setattr(e,'registration',unavailable)
    assert e.verify_observation(observed,lambda proof:Path(proof['path']))=='known_success'
    assert e.scientific_result(config,{},completed,observed)==expected
    assert e.scientific_outputs(config,observed)[0].name=='original'
    # Exercise the real closeout consumer; an unrelated row.json never becomes
    # a Cov metric projection, even when it is an exact terminal proof.
    root=tmp_path/'instance';record=root/'native-evidence/002';record.mkdir(parents=True)
    c.write(record/'experiment-config.json',config)
    monkeypatch.setattr(c,'workspace',lambda root,step:tmp_path)
    monkeypatch.setattr(author_closeout,'terminal_facts',lambda root,step:dict(scope='retained',calls=[
        dict(call_id=completed['call_id'],status='known_success',candidate_id=None,mechanism=None,
            originals=directory.relative_to(tmp_path).as_posix(),qualification='unqualified')]))
    facts=author_closeout.science_facts(root,2)
    assert facts['calls'][0]['result']==expected
    assert 'reported' not in facts['calls'][0]


def test_legacy_terminal_public_reader_does_not_load_new_projection(monkeypatch):
    monkeypatch.setattr(e,'registration',lambda *args:pytest.fail('legacy result must not load a provider'))
    completed=dict(status='known_success',returncode=0,private_native=dict(val_score=999))
    assert e.scientific_result(dict(execution_interface={}),{},completed,{})==dict(status='known_success',returncode=0)


def context_scene(tmp_path,monkeypatch):
    selected=scope();parent=dict(status='declared_root',candidate_id=None,source='none')
    accepted=dict(call_id='call-one',scope=selected,parent=parent,change_intent=None)
    c.write(tmp_path/'accepted.json',accepted)
    def archive(name):
        path=tmp_path/name;proof=e.proof(path);target=tmp_path/'host-history/objects'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():shutil.copyfile(path,target)
        return dict(proof,path=name,snapshot=target.relative_to(tmp_path).as_posix(),retention='archived')
    monkeypatch.setattr(protocol,'stage_scope',lambda root,job:selected)
    monkeypatch.setattr(history,'_execution_interface',lambda root,accepted:dict(executor='laboratory:opaque'))
    declared=ExecutionInterfaceRegistration('laboratory','1',RuntimePluginFactory(lambda context:None))
    monkeypatch.setattr(e,'registration',lambda executor:(declared,'opaque'))
    item=dict(call_id='call-one',scope=selected,status='known_success',candidate_id='specimen-alpha',
        run_id='remote:batch:alpha',parent=parent,change_intent=None,note='exact native observation',
        proofs=[dict(e.proof(tmp_path/'accepted.json'),path='accepted.json')])
    return item,archive


def test_context_string_run_success_without_model_and_multiple_artifacts(tmp_path,monkeypatch):
    item,archive=context_scene(tmp_path,monkeypatch)
    assert lineage.contexts(tmp_path,{},[item],archive)[0]['run_id']=='remote:batch:alpha'
    for label in ('spectrum','calibration'):
        (tmp_path/label).write_bytes(label.encode())
        item[label]=dict(e.proof(tmp_path/label),path=label)
    result=lineage.contexts(tmp_path,{},[item],archive)[0]
    assert len(lineage.context_artifacts(result))==2
    assert all(proof['retention']=='archived' for proof in lineage.context_artifacts(result))


def test_context_rejects_provider_archive_fields_and_changed_identity(tmp_path,monkeypatch):
    item,archive=context_scene(tmp_path,monkeypatch)
    (tmp_path/'spectrum').write_bytes(b'original')
    item['spectrum']=dict(e.proof(tmp_path/'spectrum'),path='spectrum',retention='archived',snapshot='fake')
    with pytest.raises(ValueError,match='predeclare'):
        lineage.contexts(tmp_path,{},[item],archive)
    item.pop('spectrum');item['call_id']='other-call'
    with pytest.raises(history.ObserverIntegrityError,match='accepted original'):
        lineage.contexts(tmp_path,{},[item],archive)


def test_first_managed_candidate_audit_matches_only_exact_artifact(tmp_path,monkeypatch):
    artifact=dict(path='outputs/specimen.dat',name='specimen.dat',size_bytes=8,
        snapshot='host-history/objects/specimen',retention='archived')
    declaration=dict(artifact='artifacts/specimen.dat',label='declared specimen')
    call=dict(call_id='call-alpha',scope=scope(),status='known_success',result_protocol=e.LOCAL_RESULT_PROTOCOL,
        completed=dict(path='completed.json',size_bytes=10,retention='archived'),outputs=[artifact],
        execution=dict(result=dict(status='available',metrics=[],outputs=[dict(path=declaration['artifact'])],candidates=[declaration])))
    candidate=generic_results.candidates([call])[0]
    records=[dict(step=2,observation=dict(candidates=[candidate],managed_calls=[call],contexts=[]))]
    directory=tmp_path/'audit';directory.mkdir()
    binding=dict(source=dict(objects=dict(artifact=dict(size_bytes=8))))
    c.write(directory/'binding.json',binding)
    outcome=dict(candidate_id=candidate['id'],binding_ref='bound',result=dict(status='audited'))
    monkeypatch.setattr(candidate_audit,'public_receipt',lambda root,ref:None)
    monkeypatch.setattr(candidate_audit,'_outcome',lambda root,step:outcome)
    monkeypatch.setattr(candidate_audit,'_directory',lambda root,step:directory)
    monkeypatch.setattr(candidate_audit.configuration_updates,'for_step',lambda root,step:dict(delivery_policy=dict(adapter=dict(adapter='/provider.py'))))
    refs=[dict(step=3)]
    assert candidate_audit.project_receipts(tmp_path,records,refs)[0]['qualification']=='passed'
    projection=dict(subject=dict(step=3,candidate_audit=dict(step=3)),sources=[(dict(step=3,role='A'),{},None,None)],records=records)
    c.write(directory/'outcome.json',outcome)
    # UI consumes exactly the same candidate/artifact attribution.
    ui_directory=tmp_path/'native-evidence/003/candidate-audit';ui_directory.parent.mkdir(parents=True)
    shutil.copytree(directory,ui_directory)
    assert ui_view.audit_facts(tmp_path,projection)[candidate['id']]=='公开审计通过'
    changed=copy.deepcopy(binding);changed['source']['objects']['artifact']['path']='host-history/objects/other'
    (directory/'binding.json').unlink();c.write(directory/'binding.json',changed)
    assert candidate_audit.project_receipts(tmp_path,records,refs)[0]['qualification']=='artifact_scope_unknown'


def retirement_packet(tmp_path):
    call='call-alpha';data=b'opaque prepared';source=tmp_path/'source.py';source.write_bytes(data)
    prepared_path=tmp_path/'package';prepared_path.write_bytes(data)
    prepared=dict(name=call,local_path=str(prepared_path),size_bytes=len(data),media_type='application/octet-stream')
    contract=dict(e.RETIREMENT_CONTRACT,backend_protocol=dict(native='fence-receipt-zeta'))
    raw=e.encoded(contract)
    capability=dict(key=e.RETIREMENT_KEY,kind='execution_recovery',schema_id=e.RETIREMENT_SCHEMA,
        payload_schema_version=1,media_type='application/json',size_bytes=len(raw))
    interface=dict(executor='laboratory:opaque',public_capabilities=[capability])
    authority=tmp_path/'authority.json';c.write(authority,dict(call_id=call))
    accepted=dict(call_id=call,scope=scope(),native_attempt=None,mechanism_binding=None,interface=interface,
        prepared=prepared,script=e.proof(source),execution_authority=e.proof(authority))
    c.write(tmp_path/'accepted.json',accepted)
    native=tmp_path/'unusual-native-receipt';native.write_bytes(b'NEVER-ADMITTED ZETA 73\n')
    originals=[e.proof(native)]
    assertion=dict(version=1,kind='permanent_not_submitted',call_id=call,scope=scope(),accepted=e.proof(tmp_path/'accepted.json'),
        prepared=prepared,authority=e.proof(authority),native_originals=originals)
    c.write(tmp_path/'assertion.json',assertion)
    return dict(assertion,version=2,interface=interface,capability=capability,contract=contract,
        native_attempt=None,mechanism_binding=None,assertion=e.proof(tmp_path/'assertion.json'),dispatch_intent=None)


def test_non_domain_retirement_is_offline_and_identity_bound(tmp_path,monkeypatch):
    packet=retirement_packet(tmp_path)
    monkeypatch.setattr(e,'registration',lambda *args:pytest.fail('v2 retirement must not load any provider'))
    assert e.verify_retirement(packet,lambda proof:Path(proof['path']))['call_id']=='call-alpha'
    for field,replacement in [('call_id','other'),('scope',dict(step=999)),('authority',packet['accepted']),
            ('prepared',dict(packet['prepared'],name='other'))]:
        changed=dict(packet,**{field:replacement})
        with pytest.raises(e.IntegrityError):e.verify_retirement(changed,lambda proof:Path(proof['path']))
    (tmp_path/'unusual-native-receipt').write_bytes(b'CHANGED')
    with pytest.raises(e.IntegrityError):e.verify_retirement(packet,lambda proof:Path(proof['path']))


def test_pending_capture_and_recovery_verify_text_and_binary_originals(tmp_path,monkeypatch):
    from contextlib import nullcontext
    from scidiscovery.research_loop import (execution_resume as resume,execution_authority as authority,
        execution,experiment_client,qualification as q)
    job=dict(step=2,role='W',execution_authority={'version':1})
    c.write(tmp_path/'jobs/002.json',job)
    c.write(tmp_path/'authority-contract.json',dict(version=1))
    contract=q._ref(tmp_path,tmp_path/'authority-contract.json')
    monkeypatch.setattr(authority,'contract',lambda root,job:contract)
    monkeypatch.setattr(authority,'writer',lambda *args,**kwargs:nullcontext())
    monkeypatch.setattr(execution,'origin',lambda root,step:tmp_path/'native-evidence/002')
    monkeypatch.setattr(execution,'records',lambda root,step:[])
    monkeypatch.setattr(experiment_client,'record_directory',lambda root,step:tmp_path/'records')
    call='opaque-call';directory=tmp_path/'records/calls'/call;directory.mkdir(parents=True)
    c.write(directory/'accepted.json',dict(call_id=call))
    originals={'source.py':b'print("plain source")\n','stdout.raw':b'ordinary non-JSON log\n',
        'opaque-native-record':bytes([0,255,17,128])}
    for name,data in originals.items():(directory/name).write_bytes(data)
    allocation=tmp_path/'execution-authority/calls'/call/'allocation.json'
    c.write(allocation,dict(call_id=call,binding=dict(scope=dict(job_ref=c.record_ref(tmp_path/'jobs/002.json')))))
    pending=resume._capture_pending(tmp_path,job)
    seal=dict(job_ref=c.record_ref(tmp_path/'jobs/002.json'),pending_executions=pending)
    frozen=resume.verify_pending_set(tmp_path,seal,q._ref(tmp_path,allocation))
    assert {Path(item['path']).name for item in frozen['records']}=={'accepted.json',*originals}
    (directory/'opaque-native-record').write_bytes(b'changed original')
    with pytest.raises(ValueError):resume.verify_pending_set(tmp_path,seal,q._ref(tmp_path,allocation))


@pytest.mark.process_e2e
def test_cov_prepare_defaults_produce_a_readable_observer(tmp_path,execution_registry):
    import sys
    from cov_execution import lineage as native_observer
    from scidiscovery.research_loop import research_config
    root=tmp_path/'instance';public=tmp_path/'public';public.mkdir()
    objective=public/'objective';objective.write_text('Read the declared public fixture only.')
    options=dict(root=root,objective=objective,public_root=public,launcher=['custom'],model='no-model',
        reasoning_effort='high',search_policy=None,max_tokens=1000,max_seconds=60,max_steps=4,
        execution_config=dict(executor='cov_execution:experiment',configuration=dict(run_argv=[sys.executable],
            runs_root=str(root/'runs'),evaluation_key='public-fixture',receipt_channel='backend_file')))
    settings=research_config.prepare(**options)
    observer=settings['history_observer']
    assert observer['config']==dict(runs='runs',calls='project/nodes',evaluation_key='public-fixture')
    value=history.read_adapter(observer,dict(root=str(root),step=1,config=observer['config'],materials=[],known_candidates=[],known_contexts=[]))
    assert value['coverage']['status']=='complete' and value['contexts']==[]
    conflicting=dict(options,root=tmp_path/'conflict',history_observer=dict(adapter=str(Path(native_observer.__file__).resolve()),
        contract_version=2,config=dict(runs='wrong',calls='project/nodes',evaluation_key='public-fixture')))
    conflicting['execution_config']=dict(options['execution_config'],configuration=dict(options['execution_config']['configuration'],
        runs_root=str(tmp_path/'conflict/runs')))
    with pytest.raises(ValueError,match='conflicts'):research_config.normalize(conflicting)
