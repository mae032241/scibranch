"""Original ledger and public dispatch negatives; no solver/model qualification."""
from concurrent.futures import ThreadPoolExecutor
import importlib.metadata
import json
import os
from pathlib import Path
import sys

import pytest

from scidiscovery.operations.execution import AdapterCapability, ExecutionInterfaceRegistration
from scidiscovery.operations.runtime_plugins import (RuntimePluginFactory, RuntimePluginContribution,
    HOST_EXECUTION_GATE_KEY, HOST_EXECUTION_GATE_SCHEMA, HOST_EXECUTION_GATE_CONTRACT)
from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, execution, experiments as e
from scidiscovery.research_loop import execution_authority as authority, qualification as q
from execution_fixtures import execution_registry, DifferentExternalIdentity
from test_formal_review import LAUNCHER
from test_history_handoff import finish


class GateContractFixture(DifferentExternalIdentity):
    """No policy success stub: reaching the provider is a failure in this case."""
    def __init__(self, config, services):
        super().__init__(config)
        self.gate = services.gate(self.policy) if services is not None else None

    def capabilities(self):
        return (*super().capabilities(), AdapterCapability(HOST_EXECUTION_GATE_KEY, 'execution_authority',
            HOST_EXECUTION_GATE_SCHEMA, 1, 'application/json', e.encoded(dict(HOST_EXECUTION_GATE_CONTRACT)),
            dict(fixture='Actual factory injection; no policy or execution success supplied')))

    def policy(self, raw):
        raise AssertionError('An ended native must not reach any policy/backend provider')

    def prepare(self, payload, **options):
        assert self.gate is not None
        self.gate('prepare', scope=self.config['scope'], call_id=payload.name, payload=payload, target={})
        raise AssertionError('Ended native reached preparation')


def resource_factory(context):
    return RuntimePluginContribution(execution_adapters={'fixture': GateContractFixture(
        json.loads(context.config_bytes), context.execution_host_services)})


RESOURCE_REGISTRATION = ExecutionInterfaceRegistration('resource_fixture', '1', RuntimePluginFactory(resource_factory))


@pytest.fixture
def resource_registry(tmp_path, monkeypatch, execution_registry):
    directory = tmp_path / 'resource-metadata/resource_fixture.dist-info'; directory.mkdir(parents=True)
    (directory / 'METADATA').write_text('Metadata-Version: 2.1\nName: resource-fixture\nVersion: 0\n')
    (directory / 'entry_points.txt').write_text('[scidiscovery.execution_interfaces]\nresource_fixture = test_execution_resources:RESOURCE_REGISTRATION\n')
    monkeypatch.syspath_prepend(str(directory.parent))
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join([str(directory.parent), str(Path(__file__).parent),
        os.environ.get('PYTHONPATH', '')]))


def prepare(tmp_path, *, launcher=None, executor='resource_fixture:fixture', options=None):
    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'objective.md'; objective.write_text('Retain exact scientific and execution authority boundaries.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, launcher or ['fixture'], max_tokens=4000, max_seconds=1000,
        max_steps=7, model='fixture', reasoning_effort='high', search_policy=None, experiment_mode='local', node_budget_mode='soft',
        execution_config=dict(executor=executor, configuration=options or {}),
        formal_review_policy={'version': 1}, execution_authority_policy={'version': 1},
        execution_resource_policy=dict(version=1, allowance=dict(wall_time_seconds=10, max_storage_bytes=100)),
        source_restrictions=[dict(source=str(objective),  restriction='clear')])
    return root


def test_resource_events_interleave_original_clock_and_compete_without_new_pool(tmp_path, monkeypatch, resource_registry, capsys):
    root = prepare(tmp_path); now = [100.]
    monkeypatch.setattr(budget.time, 'time', lambda: now[0])
    monkeypatch.setattr(budget.time, 'clock_gettime', lambda _: now[0])
    c.write(root / 'host-started.json', budget.clock_stamp())
    with controller._writer(root):
        budget.transition(root, 'advance', 'Original clock, not a resource grant')
    now[0] = 102.
    before = budget.snapshot(root, sample_stamp=budget.clock_stamp())
    grant = budget.execution_grant(root)
    request = q._save(root, root / 'host-runtime/accounting-fixture/request.json', dict(scope='ledger math only'))
    decision = q._save(root, root / 'host-runtime/accounting-fixture/decision.json', dict(scope='opaque ref, not an execution grant'))
    # Lower-level ledger competition only. No scientific approval/accepted native
    # is asserted; the production gate is independently required before dispatch.
    def reserve(index):
        binding = dict(call_id=f'{index:04d}-' + str(index) * 32, scope={'fixture': 'same owner'},
            native_attempt={'number': 0}, interface={'executor': 'resource_fixture:fixture'}, authority={'fixture': index})
        try:
            ref = budget.reserve_execution(root, binding=binding, request_ref=request, decision_ref=decision,
                budget=dict(wall_time_seconds=6, max_storage_bytes=60), grant_revision=grant['ref'],
                effective_allowance=dict(wall_time_seconds=10, max_storage_bytes=100))
            return binding, ref
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as workers:
        contenders = list(workers.map(reserve, (1, 2)))
    assert sum(value is not None for value in contenders) == 1
    binding, reservation = next(value for value in contenders if value is not None)
    assert budget.reserve_execution(root, binding=binding, request_ref=request, decision_ref=decision,
        budget=dict(wall_time_seconds=6, max_storage_bytes=60), grant_revision=grant['ref'],
        effective_allowance=dict(wall_time_seconds=10, max_storage_bytes=100)) == reservation
    after = budget.snapshot(root, sample_stamp=budget.clock_stamp())
    assert {key: after[key] for key in ('advance_seconds', 'max_tokens', 'max_seconds', 'seconds_remaining')} == {
        key: before[key] for key in ('advance_seconds', 'max_tokens', 'max_seconds', 'seconds_remaining')}
    assert budget.execution_accounting(root)['remaining'] == dict(wall_time_seconds=4, max_storage_bytes=40)
    with controller._writer(root):
        budget.transition(root, 'waiting', 'Original managed waiting')
    now[0] = 105.
    from scidiscovery.research_loop.cli import main
    assert main(['budget-amend', '--root', str(root), '--execution-wall-seconds', '20',
        '--execution-storage-bytes', '200', '--authorization-note', 'Explicit operator ceiling only']) == 0
    capsys.readouterr()
    revised = budget.execution_grant(root)
    assert revised['owner'] == grant['owner'] and revised['ref'] != grant['ref']
    assert budget.execution_accounting(root)['remaining'] == dict(wall_time_seconds=14, max_storage_bytes=140)
    with pytest.raises(ValueError, match='revision changed'):
        budget.reserve_execution(root, binding=binding, request_ref=request, decision_ref=decision,
            budget=dict(wall_time_seconds=6, max_storage_bytes=60), grant_revision=grant['ref'],
            effective_allowance=dict(wall_time_seconds=20, max_storage_bytes=200))
    unknown = q._save(root, root / 'host-runtime/accounting-fixture/lookup-none.json', dict(found=False, status='unknown'))
    with pytest.raises((ValueError, KeyError)):
        budget.release_execution(root, reservation=reservation, retirement=unknown)
    with controller._writer(root):
        budget.transition(root, 'paused', 'No new resource grant')
    now[0] = 200.
    assert budget.execution_grant(root)['ref'] == revised['ref']
    assert budget.execution_accounting(root)['remaining'] == dict(wall_time_seconds=14, max_storage_bytes=140)
    assert budget.snapshot(root)['advance_seconds'] == 2 and budget.snapshot(root)['waiting_seconds'] == 3
    assert budget.authorization_revision(root) == q._ref(root, root / 'settings.json')


def test_configure_rejects_old_factory_that_ignores_host_services(tmp_path, execution_registry):
    root = prepare(tmp_path, executor='local_execution:command', options=dict(run_argv=[sys.executable]))
    finish(root, 1, 'H', [], action='experiment')
    job = c.build_job(root, 2, 'W', [1]); record = execution.record(root, 2)
    intent = dict(task_ref=c.record_ref(root / 'jobs/002.json'))
    accepted_budget.accept_native(root, 2, record, intent)
    assert c.read(record / 'native-intent.json') == intent
    with pytest.raises(e.UnsupportedExecution, match='does not declare'):
        e.configure(root, 2, 'local_execution:command', dict(run_argv=[sys.executable]),
            c.workspace(root, 2) / 'work/experiment-config.json')
    assert not list(root.glob('**/accepted.json'))


@pytest.mark.process_e2e
def test_public_run_rejects_sealed_w_config_before_provider_or_reservation(tmp_path, monkeypatch, resource_registry):
    launcher = tmp_path / 'codex'; launcher.write_text('#!' + sys.executable + '\n' + LAUNCHER); launcher.chmod(0o700)
    home = tmp_path / 'native'; home.mkdir(); monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.delenv('REVIEW_REUSE_AUTHOR', raising=False); monkeypatch.delenv('REVIEW_MISSING_AUTHOR', raising=False)
    root = prepare(tmp_path, launcher=[str(launcher)])
    assert controller.run(root)['status'] == 'finish'
    # The current strategy starts W001; no hypothetical H/W/A/review step 5
    # exists. Reuse the configuration made while that actual W was active.
    job = c.read_job(root / 'jobs/001.json'); record = execution.origin(root, 1)
    config = c.read(record / 'experiment-config.json')
    assert job['role'] == 'W' and job['research_strategy_version'] == 3
    assert config['scope']['job_ref'] == c.record_ref(root / 'jobs/001.json')
    assert config['native_attempt'] == dict(number=0, execution_key=execution.identity(root, 1, 0))
    assert (record / 'result-identity.json').is_file()
    assert not job['execution_qualifications']  # No review or authority is invented.
    original = c.workspace(root, 1) / 'artifacts/model.txt'; identity = e.proof(original)
    from scidiscovery.operations.execution import LocalFileDescriptor
    payload = LocalFileDescriptor(name='ignored', local_path=str(original), size_bytes=identity['size_bytes'], media_type='application/octet-stream')
    before = budget.execution_accounting(root)
    assert not e.Records(config).calls() and not before['reservations']
    head = accepted_budget.read(root)
    assert accepted_budget.authority_calls(head, 1) == {}
    checked = []
    require_active = e._require_new_execution
    def ended(config_):
        checked.append(config_)
        return require_active(config_)
    def forbidden(*args, **kwargs):
        pytest.fail('Sealed W reached factory/provider, authority allocation or resource reservation')
    monkeypatch.setattr(e, '_require_new_execution', ended)
    monkeypatch.setattr(e, 'load', forbidden)
    monkeypatch.setattr(e, '_adapter', forbidden)
    monkeypatch.setattr(GateContractFixture, 'prepare', forbidden)
    monkeypatch.setattr(GateContractFixture, 'submit', forbidden)
    monkeypatch.setattr(GateContractFixture, 'policy', forbidden)
    monkeypatch.setattr(authority, '_allocate_call', forbidden)
    monkeypatch.setattr(authority, 'record_policy_decision', forbidden)
    monkeypatch.setattr(budget, 'execution_grant', forbidden)
    monkeypatch.setattr(budget, 'reserve_execution', forbidden)
    monkeypatch.setattr(budget, 'bind_execution', forbidden)
    with pytest.raises(e.UnsupportedExecution, match='ended work uses original-call recovery'):
        e.dispatch_descriptor(config, payload)
    assert checked == [config]
    assert not e.Records(config).calls()
    assert budget.execution_accounting(root) == before
    assert accepted_budget.authority_calls(accepted_budget.read(root), 1) == {}


@pytest.mark.parametrize('phase',['prepare','submit'])
@pytest.mark.parametrize('boundary',['eligibility','admission'])
def test_dispatch_checks_eligibility_and_admission_before_provider_or_reservation(tmp_path,monkeypatch,phase,boundary):
    """Gate-order negative control only; no backend or scientific success is mocked."""
    from test_execution_authority import declared_request
    from scidiscovery.operations.execution import LocalFileDescriptor
    root,secret,arguments=declared_request(tmp_path)
    request_ref=authority.create_request(root,**arguments)
    request_id=Path(request_ref['path']).parent.name
    job=q._read(root,q._ref(root,root/'jobs/002.json'))
    intent=arguments['native_intent'];subject=arguments['subject'];review_ref=arguments['review_ref']
    alias={key:subject['material']['source' if key=='path' else key] for key in ('path','size_bytes')}
    payload=LocalFileDescriptor(name=arguments['call_id'],local_path=alias['path'],
        size_bytes=alias['size_bytes'],media_type='application/octet-stream')
    config=dict(scope=dict(step=2,job_ref=c.record_ref(root/'jobs/002.json')),
        native_attempt=dict(number=0),execution_interface=dict(executor='fixture-declaration-only'))
    allocation=dict(call_id=arguments['call_id'],native_intent=intent,
        binding=dict(scope=config['scope'],native_attempt=config['native_attempt'],interface=config['execution_interface'],
            subject=subject,review=review_ref,payload_alias=alias))
    q._save(root,root/'execution-authority/calls'/arguments['call_id']/'allocation.json',allocation)
    (root/'execution-authority/host-key-source.json').touch()
    monkeypatch.setattr(authority,'_host_context',lambda config_:(root,job,intent))
    monkeypatch.setattr(authority,'host_secret',lambda root_:secret)
    monkeypatch.setattr(authority,'_selected_subject',lambda *args:(subject,review_ref,alias))
    monkeypatch.setattr(q,'execution_payload',lambda *args:alias)
    original_permit=dict(request=request_ref)
    if phase=='submit':monkeypatch.setattr(authority,'_permit',lambda *args:original_permit)
    def forbidden(*args,**kwargs):pytest.fail('Denied dispatch reached provider, reservation or execution binding')
    monkeypatch.setattr(budget,'execution_grant',forbidden)
    monkeypatch.setattr(budget,'reserve_execution',forbidden)
    monkeypatch.setattr(budget,'bind_execution',forbidden)
    if boundary=='eligibility':
        def missing(*args,**kwargs):raise ValueError('Scientific originals unavailable')
        monkeypatch.setattr(c,'_validate_inputs',missing)
        monkeypatch.setattr(authority,'_remaining',forbidden)
        message='Scientific originals unavailable'
    else:
        # Isolate admission after its separately checked eligibility boundary.
        monkeypatch.setattr(authority,'require_request_eligibility',lambda *args:None)
        def unavailable(*args,**kwargs):raise ValueError('Current dispatch admission unavailable')
        monkeypatch.setattr(authority,'_remaining',unavailable)
        message='Current dispatch admission unavailable'
    with pytest.raises(ValueError,match=message):
        authority._gate(config,forbidden,phase,scope=config['scope'],call_id=arguments['call_id'],
            payload=payload,target=arguments['target'],prior_receipt={'fixture':'prior permission'} if phase=='submit' else None)
    assert authority.status(root,request_id,secret=secret)['state']=='pending'
    assert not list(root.glob('**/prepare-intent.json'))
    assert not list(root.glob('**/accepted.json')) and not list(root.glob('**/submission.json'))


@pytest.mark.parametrize('phase',['prepare','submit'])
def test_gate_rejects_actual_request_for_another_payload_before_decision_or_effect(tmp_path,monkeypatch,phase):
    """A real hashed request cannot borrow eligibility checked for another payload."""
    import hashlib
    from test_execution_authority import declared_request
    from scidiscovery import execution_security as security
    from scidiscovery.artifact_agent.schema.execution import ExecutionAdmission
    from scidiscovery.operations.execution import LocalFileDescriptor
    root,secret,arguments=declared_request(tmp_path)
    job=q._read(root,q._ref(root,root/'jobs/002.json'))
    subject=arguments['subject'];review_ref=arguments['review_ref'];intent=arguments['native_intent']
    alias={key:subject['material']['source' if key=='path' else key] for key in ('path','size_bytes')}
    Path(alias['path']).write_text('Gate-order transport fixture; no scientific qualification')
    policy={};owner_key='e'*64
    admission=ExecutionAdmission(outcome='policy',policy=policy,
        policy_digest=hashlib.sha256(security.canonical(policy)).hexdigest(),budget=arguments['resources'],
        allowance=arguments['resources'],outside_allowance='deny',budget_key=owner_key,
        reason='Scope-binding negative control; no backend or scientific qualification is supplied')
    wrong=dict(alias,path=str(tmp_path/'another-private-package.py'),size_bytes=53)
    request_ref=authority.create_request(root,**dict(arguments,policy=admission.model_dump(mode='json')),payload_binding=wrong)
    request_id=Path(request_ref['path']).parent.name
    assert authority.request(root,request_id)[0]['payload']==wrong
    payload=LocalFileDescriptor(name=arguments['call_id'],local_path=alias['path'],
        size_bytes=alias['size_bytes'],media_type='application/octet-stream')
    config=dict(scope=dict(step=2,job_ref=c.record_ref(root/'jobs/002.json')),
        native_attempt=dict(number=0),execution_interface=dict(executor='fixture-declaration-only'))
    allocation=dict(call_id=arguments['call_id'],native_intent=intent,
        binding=dict(scope=config['scope'],native_attempt=config['native_attempt'],interface=config['execution_interface'],
            subject=subject,review=review_ref,payload_alias=alias))
    q._save(root,root/'execution-authority/calls'/arguments['call_id']/'allocation.json',allocation)
    (root/'execution-authority/host-key-source.json').touch()
    monkeypatch.setattr(authority,'_host_context',lambda config_:(root,job,intent))
    monkeypatch.setattr(authority,'host_secret',lambda root_:secret)
    monkeypatch.setattr(authority,'_selected_subject',lambda *args:(subject,review_ref,alias))
    monkeypatch.setattr(q,'execution_payload',lambda *args:alias)
    checked=[]
    monkeypatch.setattr(authority,'require_request_eligibility',lambda root_,value:checked.append(value))
    grant=dict(owner=dict(key=owner_key),ref=arguments['grant_revision'])
    monkeypatch.setattr(budget,'execution_grant',lambda *args,**kwargs:grant)
    original=dict(request=request_ref,admission=admission.model_dump(mode='json'),grant_revision=grant['ref'])
    monkeypatch.setattr(authority,'_permit',lambda *args:original)
    def forbidden(*args,**kwargs):pytest.fail('Mismatched request reached decision, resource reservation or backend binding')
    monkeypatch.setattr(authority,'record_policy_decision',forbidden)
    monkeypatch.setattr(budget,'reserve_execution',forbidden)
    monkeypatch.setattr(budget,'bind_execution',forbidden)
    class Recovery:
        context=None
        def check(self):return budget.clock_stamp(),100.
        def job_remaining(self):return 100.
        def request(self,*args):return request_ref
    with pytest.raises(ValueError,match='eligible dispatch scope'):
        authority._gate(config,lambda raw:admission,phase,scope=config['scope'],call_id=arguments['call_id'],
            payload=payload,target=arguments['target'],prior_receipt={'fixture':'prior permission'} if phase=='submit' else None,
            recovery=Recovery())
    assert checked and checked[0]['payload']==alias
    assert authority.status(root,request_id,secret=secret)['state']=='pending'
    assert not list(root.glob('**/decision.json')) and not list(root.glob('**/prepare-intent.json'))
    assert not list(root.glob('**/accepted.json')) and not list(root.glob('**/submission.json'))
