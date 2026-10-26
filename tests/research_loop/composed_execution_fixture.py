"""Synthetic registered opaque/async execution contract fixture; no remote service."""
import json
from pathlib import Path
from scidiscovery.research_loop import experiments as e
from scidiscovery.operations.execution import AdapterCapability, ExecutionInterfaceRegistration, LifecycleCapabilities, LocalFileDescriptor, execution_status
from scidiscovery.operations.runtime_plugins import RuntimePluginContribution, RuntimePluginFactory


class Remote:
    def __init__(self, config):
        self.config = config
        self.records = e.Records(config)
        self.accepted = None
    @property
    def result_protocol(self): return self.config.get('result_protocol')
    def lifecycle_capabilities(self):
        return LifecycleCapabilities(lookup_submission=True, query=True, collect=self.config.get('collect', True), atomic_admission=True)
    def capabilities(self):
        schema=dict(type='object',properties=dict(observation=dict(type='string',const='composed-original')),required=['observation'],additionalProperties=False)
        return (AdapterCapability('opaque_input','execution','fixture.composed-input.v1',1,'application/schema+json',e.encoded(schema),dict(input='JSON object matching the public schema')),)
    def prepare(self, payload, **kwargs):
        if self.config.get('require_contract'):
            import jsonschema
            jsonschema.validate(json.loads(e.read(payload.local_path)),json.loads(self.capabilities()[0].content))
        return payload
    def acceptance_metadata(self, payload, **kwargs): return dict(parent=dict(candidate_id=None), change_intent=None)
    def dispatch_lease(self, accepted): self.accepted = accepted
    def artifact_path(self, call):
        from scidiscovery.research_loop import contracts as c, experiment_client, shared_input_views
        root = experiment_client.root_for_configuration(self.config, allow_standalone=True)
        if root is None: return self.records.root / 'synthetic-backend-outputs' / call / 'original'
        work = c.workspace(root, self.config['scope']['step'])
        return shared_input_views.dynamic_root(root, work, 'retained-experiments') / call / 'outputs/original'
    def lookup_submission(self, descriptor):
        if not (self.records.directory(descriptor.name) / 'backend-accepted.json').exists(): return None
        return 'remote-' + descriptor.name, self.status_details(descriptor.name)['state']
    def submit(self, descriptor):
        assert self.accepted['call_id'] == descriptor.name
        assert self.accepted['prepared'] == descriptor.model_dump(mode='json')
        found = self.lookup_submission(descriptor)
        if found: return found
        # Retain a backend-produced output from this exact submitted input;
        # later collection never reopens mutable task code or old snapshots.
        data = e.read(descriptor.local_path)
        assert len(data) == descriptor.size_bytes
        artifact = self.artifact_path(descriptor.name)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        with artifact.open('xb') as stream: stream.write(data)
        artifact.chmod(0o400)
        e.write(self.records.directory(descriptor.name) / 'backend-accepted.json', dict(external_run_id='remote-' + descriptor.name,
            call_id=descriptor.name,scope=self.accepted['scope'],prepared=descriptor.model_dump(mode='json'),state=self.config.get('query_state','running')))
        if self.config.get('synchronous') or self.config.get('synchronous_first') and len(self.records.calls())==1: self.complete(descriptor.name)
        return 'remote-' + descriptor.name, self.status_details(descriptor.name)['state']
    def complete(self, call):
        accepted = self.records.accepted(call)
        directory = self.records.directory(call)
        native=dict(native_state=self.config['terminal_state']) if self.config.get('terminal_state') else {}
        e.write(directory / 'backend-terminal.json', dict(call_id=call, scope=accepted['scope'], status=self.config.get('terminal_status','known_success'),native_payload='retained-native-terminal-only',**native))
        value = dict(status=self.config.get('terminal_status','known_success'), proofs=[e.proof(directory / 'backend-terminal.json')], note='Synthetic backend terminal',**native)
        private = self.config.get('private')
        if private == 'native':
            value['result'] = dict(status='available', metrics=[], candidates=[dict(native_model_id='opaque-model-1')])
        elif private == 'local-like':
            value['result'] = dict(status='available', metrics=[dict(name='quality', value=1.0, evaluation_key='backend/public', direction='max')],
                candidates=[dict(label='native-model', metric='quality', artifact='artifacts/model.bin')],
                outputs=[dict(path='artifacts/model.bin', label='Backend native output')])
        self.records.publish(call, 'completed.json', value)
    def result(self, call):
        path = self.records.directory(call) / 'completed.json'
        if not path.exists(): return dict(call_id=call, status='unknown', note='Accepted backend task still running')
        value = json.loads(e.read(path))
        terminal = json.loads(e.read(self.records.directory(call) / 'backend-terminal.json'))
        if (terminal['call_id'] != call or terminal['scope'] != self.config['scope'] or terminal['status'] != value['status']
            or terminal.get('native_state')!=value.get('native_state')
            or ((self.records.directory(call)/'submission.json').exists() and self.records.external(call) != json.loads(e.read(self.records.directory(call)/'backend-accepted.json'))['external_run_id'])):
            raise e.IntegrityError('Original backend terminal binding changed')
        for proof in value['proofs']:
            if e.proof(proof['path']) != proof:raise e.IntegrityError('Original backend terminal proof changed')
        return value
    def status_details(self, call):
        if (self.records.root/'forbid-backend-reads').exists():raise e.IntegrityError('Fixture forbids another native query')
        accepted=self.records.accepted(call)
        backend=json.loads(e.read(self.records.directory(call)/'backend-accepted.json'))
        if (backend.get('call_id')!=call or backend.get('scope')!=accepted['scope']
                or backend.get('prepared')!=accepted['prepared']
                or backend.get('external_run_id')!='remote-'+call):
            raise e.IntegrityError('Original backend acceptance binding changed')
        value = self.result(call)
        return dict(state=value.get('native_state') or {'known_success':'succeeded','known_failed':'failed'}.get(value['status'],backend['state']), call_id=call)
    def status(self, external): return self.status_details(external.removeprefix('remote-'))['state']
    def collect(self, external):
        if not self.config.get('collect', True): raise e.UnsupportedExecution('Backend does not declare collection')
        call = external.removeprefix('remote-')
        assert execution_status(self.status(external)) in {'known_success','known_failed'}
        path = self.artifact_path(call)
        proof = e.proof(path)
        return (LocalFileDescriptor(name='original', local_path=str(path), size_bytes=proof['size_bytes'], media_type='application/octet-stream'),)
    def collect_original(self, call): return self.collect(self.records.external(call))
    def cancel(self, external): raise e.UnsupportedExecution('Cancellation is not declared')
    def cancel_original(self, call): return self.cancel(self.records.external(call))
    def public_result(self, result):
        return {key: result[key] for key in ('call_id','status','note','native_state') if key in result}


def factory(context):
    return RuntimePluginContribution(execution_adapters={'fixture': Remote(json.loads(context.config_bytes))})


REGISTRATION = ExecutionInterfaceRegistration('composed_execution', '1', RuntimePluginFactory(factory))
