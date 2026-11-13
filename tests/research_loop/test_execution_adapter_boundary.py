"""Non-domain adapter contracts; no model, solver or external transport."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest

from execution_fixtures import DifferentExternalIdentity, execution_registry, remote_registry
from scidiscovery.research_loop import (candidate_audit, contracts as c,
    experiments as e, generic_results, history, protocol, ui_view)


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
    def unavailable(*args,**kwargs):raise AssertionError('A frozen terminal must not load a provider')
    monkeypatch.setattr(e,'registration',unavailable)
    assert e.verify_observation(observed,lambda proof:Path(proof['path']))=='known_success'
    assert e.scientific_result(config,{},completed,observed)==expected
    assert e.scientific_outputs(config,observed)[0].name=='original'


def test_legacy_terminal_public_reader_does_not_load_new_projection(monkeypatch):
    monkeypatch.setattr(e,'registration',lambda *args:pytest.fail('legacy result must not load a provider'))
    completed=dict(status='known_success',returncode=0,private_native=dict(val_score=999))
    assert e.scientific_result(dict(execution_interface={}),{},completed,{})==dict(status='known_success',returncode=0)


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
    binding=dict(source=dict(objects=dict(artifact=dict(path='outputs/specimen.dat',size_bytes=8))))
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
