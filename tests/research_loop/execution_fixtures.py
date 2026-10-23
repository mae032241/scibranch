"""Source discovery metadata only; installed checks use the wheel's real entries."""
import importlib.metadata
import os
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def execution_registry(tmp_path,monkeypatch):
    declared = {item.name for item in importlib.metadata.entry_points().select(group='scidiscovery.execution_interfaces')}
    entries = {
        'local_execution': 'scidiscovery.research_loop.local_experiments:REGISTRATION',
        'cov_execution': 'cov_execution.experiments:REGISTRATION',
    }
    missing = {name: target for name, target in entries.items() if name not in declared}
    if not missing:
        source_root=Path(e.__file__).parents[3]
        return source_root if (source_root/'pyproject.toml').is_file() else None
    metadata=tmp_path/'discovery/scidiscovery_execution_source.dist-info'
    metadata.mkdir(parents=True)
    (metadata/'METADATA').write_text('Metadata-Version: 2.1\nName: scidiscovery-execution-source\nVersion: 0\n')
    (metadata/'entry_points.txt').write_text('[scidiscovery.execution_interfaces]\n' +
        ''.join(name + ' = ' + target + '\n' for name, target in missing.items()))
    root=str(metadata.parent)
    monkeypatch.syspath_prepend(root)
    monkeypatch.setenv('PYTHONPATH',root+os.pathsep+os.environ.get('PYTHONPATH',''))
    return metadata.parent


# Contract fixture only: no solver, native process or scientific result claim.
from scidiscovery.operations.execution import AdapterCapability, ExecutionInterfaceRegistration, LifecycleCapabilities, LocalFileDescriptor
from scidiscovery.operations.runtime_plugins import RuntimePluginContribution, RuntimePluginFactory
from scidiscovery.research_loop import experiments as e
import json


class DifferentExternalIdentity:
    def __init__(self,config):self.records=e.Records(config);self.config=config;self.accepted=None
    def lifecycle_capabilities(self):return LifecycleCapabilities(lookup_submission=True,query=True,collect=True,atomic_admission=True)
    def capabilities(self):
        schema=dict(type='object',properties=dict(message=dict(type='string',const='remote-original')),required=['message'],additionalProperties=False)
        detail='x'*self.config.get('contract_padding',0)
        if detail:schema['description']=detail
        return (AdapterCapability('opaque_input','execution','fixture.remote-input.v1',1,'application/schema+json',e.encoded(schema),dict(input='JSON object matching the public schema',detail=detail)),)
    def prepare(self,payload,**options):
        if self.config.get('require_contract'):
            import jsonschema
            jsonschema.validate(json.loads(e.read(payload.local_path)),json.loads(self.capabilities()[0].content))
        return payload
    def acceptance_metadata(self,payload,**options):return dict(parent=dict(candidate_id=None),change_intent=None)
    def dispatch_lease(self,accepted):self.accepted=accepted
    def remote_path(self,call):
        if self.config.get('proof_directory'):
            root=Path(self.config['proof_directory']);root.mkdir(exist_ok=True)
            return root/(call+'.json')
        return self.records.directory(call)/'remote-terminal.json'
    def submit(self,descriptor):
        call=descriptor.name
        found=self.lookup_submission(descriptor)
        if found:return found
        assert self.accepted['call_id']==call
        status=self.config.get('outcome','known_success')
        remote=dict(call_id=call,scope=self.config['scope'],external_run_id='remote-'+call,
            mechanism_binding=self.accepted.get('mechanism_binding'),status=status)
        e.write(self.remote_path(call),remote)
        self.records.publish(call,'completed.json',dict(call_id=call,scope=self.config['scope'],status=status,
            proofs=[e.proof(self.records.directory(call)/'accepted.json'),e.proof(self.remote_path(call))],
            note='Synthetic external identity contract fixture',
            **(dict(result=dict(status='available',metrics=[],outputs=[]),
                outputs=[dict(e.proof(self.remote_path(call)),name='native-scientific-name')])
               if self.config.get('include_result') else {})))
        return 'remote-'+call,self.records.state(call)
    def lookup_submission(self,descriptor):
        if not (self.records.root/'calls'/descriptor.name/'completed.json').exists():return None
        assert self.records.accepted(descriptor.name)['prepared']==descriptor.model_dump(mode='json')
        return 'remote-'+descriptor.name,'succeeded'
    def status(self,external_run_id):
        assert external_run_id.startswith('remote-')
        return self.records.state(external_run_id.removeprefix('remote-'))
    def status_details(self,call_id):
        self.result(call_id)
        return dict(state=self.records.state(call_id),call_id=call_id)
    def result(self,call_id):
        value=json.loads(e.read(self.records.directory(call_id)/'completed.json'))
        accepted=self.records.accepted(call_id)
        remote=json.loads(e.read(self.remote_path(call_id)))
        if (remote['external_run_id']!='remote-'+call_id or remote['status']!=value['status']
                or any(remote.get(key)!=accepted.get(key) for key in ('call_id','scope','mechanism_binding'))):
            raise e.IntegrityError('Remote terminal binding changed')
        for item in value['proofs']:
            if e.proof(item['path'])!=item:raise e.IntegrityError('Remote terminal proof changed')
        return value
    def public_result(self,result):return {key:result[key] for key in ('call_id','status','note')}
    def cancel(self,external_run_id):raise e.UnsupportedExecution('Fixture cancellation unavailable')
    def cancel_original(self,call_id):return self.cancel(self.records.external(call_id))
    def collect(self,external_run_id):
        assert external_run_id.startswith('remote-')
        path=self.records.directory(external_run_id.removeprefix('remote-'))/'source.py';item=e.proof(path)
        descriptor=LocalFileDescriptor(name=self.config.get('collect_name','original'),local_path=str(path),
            size_bytes=item['size_bytes'],media_type='application/octet-stream')
        return (descriptor,descriptor) if self.config.get('duplicate_collection') else (descriptor,)
    def collect_original(self,call_id):return self.collect(self.records.external(call_id))


def remote_factory(context):
    return RuntimePluginContribution(execution_adapters={'fixture':DifferentExternalIdentity(json.loads(context.config_bytes))})


REMOTE_REGISTRATION=ExecutionInterfaceRegistration('remote_execution','1',RuntimePluginFactory(remote_factory))


@pytest.fixture
def remote_registry(tmp_path,monkeypatch,execution_registry):
    if list(importlib.metadata.entry_points().select(group='scidiscovery.execution_interfaces',name='remote_execution')):
        return
    metadata=tmp_path/'remote-discovery/remote_execution_contract.dist-info';metadata.mkdir(parents=True)
    (metadata/'METADATA').write_text('Metadata-Version: 2.1\nName: remote-execution-contract\nVersion: 0\n')
    (metadata/'entry_points.txt').write_text('[scidiscovery.execution_interfaces]\nremote_execution = execution_fixtures:REMOTE_REGISTRATION\n')
    monkeypatch.syspath_prepend(str(metadata.parent))
    monkeypatch.setenv('PYTHONPATH',str(metadata.parent)+os.pathsep+str(Path(__file__).parent)+os.pathsep+os.environ.get('PYTHONPATH',''))
