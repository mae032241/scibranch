"""F-32/F-33: capacity rejection, exact recovery and limited scientific delivery."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from scidiscovery.agent_execution_settings import AgentSettings
from scidiscovery.operation_contract import DiagnosticError
from scidiscovery.artifact_agent.interfaces.mcp_root_shared import RootToolError
from scidiscovery.plugin_runtime.submission import SubmissionNotCreated
from tests.operations.test_experiment_continuation import continuation


def _service(f):
    return f.coordinator.worker_services['tcad_artifact:tcad.development_debug']


def _debug(f, name):
    return f.worker.call_tool('worker_experiment_debug', {
        'implementation': f.alias, 'name': name, 'mode': 'preflight'})


def _ledger(service):
    return json.loads(next((service.exchange_root / 'budgets').glob('*.json')).read_bytes())


def test_distinct_review_subjects_share_operation_without_sharing_capacity_identity(tmp_path, monkeypatch):
    f = continuation(tmp_path, monkeypatch)
    subject = next(i.artifact_name for i in f.runs.status(f.run_id).inputs if i.source_name == f.alias)
    requests = [dict(name=name, operation_id='science.object.review.v1',
        inputs=[dict(port='subject', artifact_names=[source])], instruction='Assess this exact object.')
        for name, source in [('Al-review', subject), ('Ga-review', 'observe.output')]]
    ids = []
    for request in requests:
        assert f.root.call_tool('operation_invoke', request)['result']['state'] == 'queued'
        ids.append(f.runtime.scheduler_bindings.resolve(instance=f.instance.instance_id, namespace='run', name=request['name']))
    assert len(set(ids)) == 2
    assert f.runs.status(ids[0]).inputs[0].artifact_ref != f.runs.status(ids[1]).inputs[0].artifact_ref
    # A genuine configured capacity limit remains atomically enforced and repairable.
    f.runs.agent_settings = AgentSettings(scheduling={'max_active_runs': 3})
    with pytest.raises(RootToolError) as caught:
        f.root.call_tool('operation_invoke', {**requests[0], 'name': 'capacity-full'})
    assert caught.value.details[0]['code'] == 'run_capacity_busy'
    assert caught.value.details[0]['repairable'] is True
    assert len(f.runs.list(instance_id=f.instance.instance_id)) == 4  # completed origin + current + two reviews
    # Idempotence bypasses new admission: it must still return the exact queued request.
    assert f.root.call_tool('operation_invoke', requests[0])['result']['state'] == 'queued'
    from scidiscovery.artifact_agent.interfaces.mcp_local_worker import LocalWorkerMCPRouter
    compiled = f.catalog.operation('science.object.review.v1')
    for run_id, request in zip(ids, requests, strict=True):
        caller = ('separate-reviewers', request['name'])
        f.runs.worker_connections.attach(run_id=run_id, platform_session=caller[0], thread_id=caller[1])
        worker = LocalWorkerMCPRouter(f.runs, operation_id=compiled.spec.operation_id,
            operation_digest=compiled.digest, run_id=run_id, trusted_caller=caller)
        worker.call_tool('worker_open_assignment', {})
        assert worker._run_id == run_id and f.runs.status(run_id).state == 'running'


@pytest.mark.parametrize('reason', ['capacity_exhausted', 'local_validation_failed'])
def test_debug_definite_rejection_releases_only_its_reservation(tmp_path, monkeypatch, reason):
    f = continuation(tmp_path, monkeypatch)
    service = _service(f)
    before = _ledger(service)
    def reject(submission):
        raise SubmissionNotCreated(submission.sha256, reason)
    monkeypatch.setattr(service.adapter, 'submit', reject)
    with pytest.raises(DiagnosticError) as caught:
        _debug(f, 'rejected')
    assert caught.value.details[0]['code'] == 'tcad_debug_not_submitted', repr(caught.value.__cause__)
    assert caught.value.details[0]['repairable'] is True
    after = _ledger(service)
    assert after['reserved_wall_seconds'] == before['reserved_wall_seconds']
    assert after['runs']['rejected']['submission_absent'] is True
    assert f.worker.call_tool('worker_experiment_debug', {'action': 'status', 'name': 'rejected'}) == {
        'name': 'rejected', 'state': 'cancelled', 'submission': 'not_submitted', 'allowance_reserved': False}
    assert not f.coordinator._domain_active(f.runs.status(f.run_id))


def test_unknown_survives_blocked_report_and_root_can_fence_exact_request(tmp_path, monkeypatch):
    f = continuation(tmp_path, monkeypatch)
    service = _service(f)
    def lost(submission):
        raise TimeoutError('acknowledgement lost')
    monkeypatch.setattr(service.adapter, 'submit', lost)
    monkeypatch.setattr(service.adapter, 'lookup_submission', lambda _: None)
    retired = []
    monkeypatch.setattr(service.adapter, 'retire_submission', lambda d: retired.append(d.sha256), raising=False)
    with pytest.raises(DiagnosticError) as caught:
        _debug(f, 'uncertain')
    assert caught.value.details[0]['code'] == 'tcad_debug_submission_unknown'
    reserved = _ledger(service)['reserved_wall_seconds']
    with pytest.raises(DiagnosticError):
        _debug(f, 'replacement')
    assert 'replacement' not in _ledger(service)['reservations']
    (f.workspace / 'output/result.json').write_text(json.dumps({'schema_version': 1, 'payload': {
        'outcome': 'blocked', 'summary': 'Submission confirmation unavailable.',
        'limitations': ['Diagnostic uncertain: no scientific result is claimed.'], 'remaining_question': '',
        'adopted_stages': []}}))
    assert f.worker.call_tool('worker_submit_result', {})['state'] == 'completed'
    assert _ledger(service)['reserved_wall_seconds'] == reserved
    status = f.root.call_tool('run_status', {'name': 'continuation', 'intent': 'status'})
    assert status['attention_required']['code'] == 'experiment_diagnostic_submission_unknown'
    issue = status['attention_required']['issues'][0]
    assert issue['diagnostic_name'] == 'uncertain'
    assert issue['read_request'] == {'name': 'run_diagnostic', 'surface': 'execution', 'arguments': {
        'name': 'continuation', 'diagnostic_name': 'uncertain', 'action': 'status'}}
    request = {'name': 'continuation', 'diagnostic_name': 'uncertain'}
    assert f.root.call_tool('run_diagnostic', request, surface='execution')['state'] == 'submission_unknown'
    result = f.root.call_tool('run_diagnostic', {**request, 'action': 'cancel'}, surface='execution')
    assert result['submission'] == 'not_submitted' and result['allowance_reserved'] is False
    assert len(retired) == 1
    assert _ledger(service)['reserved_wall_seconds'] == reserved - 1
    assert 'attention_required' not in f.root.call_tool('run_status', {'name': 'continuation', 'intent': 'status'})


def test_unknown_report_cannot_claim_success_or_omit_limitations(tmp_path, monkeypatch):
    f = continuation(tmp_path, monkeypatch)
    service = _service(f)
    monkeypatch.setattr(service.adapter, 'submit', lambda _: (_ for _ in ()).throw(TimeoutError('lost ack')))
    monkeypatch.setattr(service.adapter, 'lookup_submission', lambda _: None)
    monkeypatch.setattr(service.adapter, 'lookup_submission', lambda _: None)
    with pytest.raises(DiagnosticError):
        _debug(f, 'uncertain')
    for outcome, limitations in [('completed', ['Uncertain diagnostic']), ('blocked', [])]:
        (f.workspace / 'output/result.json').write_text(json.dumps({'schema_version': 1, 'payload': {
            'outcome': outcome, 'summary': 'Unavailable.', 'limitations': limitations,
            'remaining_question': '', 'adopted_stages': []}}))
        assert f.worker.call_tool('worker_submit_result', {})['state'] == 'rejected'
    assert f.runs.status(f.run_id).state == 'running'


def test_root_cancellation_recovers_existing_job_instead_of_releasing_unknown(tmp_path, monkeypatch):
    from tests.operations.test_debug_readonly_status import fixture
    service, context, path = fixture(tmp_path, reservation=True)
    ledger = json.loads(path.read_bytes())
    ledger['reservations']['init']['retirement_supported'] = True
    path.write_text(json.dumps(ledger))
    calls = []
    monkeypatch.setattr(service.adapter, 'retire_submission', lambda _: ('existing', 'running'), raising=False)
    monkeypatch.setattr(service.adapter, 'status', lambda _: 'running')
    monkeypatch.setattr(service.adapter, 'cancel', lambda job: calls.append(job) or 'cancelled', raising=False)
    inputs = (SimpleNamespace(port_name='research_objective', artifact_ref=context.input_ref('')),)
    assert service.experiment_activity({'private-run'}, inputs, cancel=True, name='init') is False
    assert calls == ['existing']
    retained = json.loads(path.read_bytes())['runs']['init']
    assert retained['external_run_id'] == 'existing' and not retained.get('submission_absent')


def test_submission_wire_rejects_wrong_identity_and_untyped_messages():
    assert SubmissionNotCreated.from_wire({'state': 'not_created', 'submission_sha256': 'b'*64,
        'reason': 'capacity_exhausted'}, expected_sha256='a'*64) is None
    assert SubmissionNotCreated.from_wire({'message': 'remote runner concurrent limit is reached'},
        expected_sha256='a'*64) is None


def test_remote_capacity_rejection_is_structured_before_creation(tmp_path, monkeypatch):
    from tcad_artifact import remote_runner_py36 as runner
    raw = runner._canonical({'tool_profile': 'fixture', 'solver_kind': 'sprocess',
        'capability_sha256': 'a'*64, 'input_archive': {'local_path': str(tmp_path / 'archive'),
        'sha256': 'b'*64, 'size_bytes': 0}})
    digest = hashlib.sha256(raw).hexdigest()
    config = {'state_root': str(tmp_path / 'state'), 'result_root': str(tmp_path / 'runs'),
        'exchange_root': str(tmp_path), 'runner': {'transfer_chunk_bytes': 1024}, 'max_concurrent_runs': 1}
    (tmp_path / 'runs/existing').mkdir(parents=True)
    (tmp_path / 'runs/existing/running').touch()
    monkeypatch.setattr(runner, '_read_bound_descriptor', lambda *_: raw)
    monkeypatch.setattr(runner, '_validate_job', lambda *_: None)
    monkeypatch.setattr(runner, '_validate_authorization', lambda *_: None)
    monkeypatch.setattr(runner, '_tool', lambda *_: {'solver_kind': 'sprocess'})
    monkeypatch.setattr(runner, '_capability_sha256', lambda *_: 'a'*64)
    monkeypatch.setattr(runner, '_file_identity', lambda *_: ('b'*64, 0))
    response = runner._rpc(config, {'request': {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': 'tcad_submit_retirable', 'arguments': {'submission': {'sha256': digest}}}}})
    assert response['error']['data']['submission_rejection'] == {
        'state': 'not_created', 'submission_sha256': digest, 'reason': 'capacity_exhausted'}
    assert not (tmp_path / 'runs' / ('run_' + digest[:32])).exists()


def test_formal_submission_uses_same_definite_rejection_and_releases_budget(tmp_path, monkeypatch):
    from tests.operations.test_experiment_approval_handoff import experiment, start
    from tests.operations.test_approval_wait_recovery import execution
    f = experiment(tmp_path, monkeypatch, seconds=60)
    original_submit = f.adapter.submit
    def reject(submission, **kwargs):
        raise SubmissionNotCreated(submission.sha256, 'capacity_exhausted')
    monkeypatch.setattr(f.adapter, 'submit', reject)
    with pytest.raises(DiagnosticError):
        start(f)
    eid = execution(f).object_id
    summary = f.coordinator.bridge.submissions.summary(eid)
    assert summary['state'] == 'failed'
    assert not f.runtime.executions.prepared_submission(eid)
    assert not f.runtime.executions.budget_accounting(eid).get('unsettled_reservation')
    monkeypatch.setattr(f.adapter, 'submit', original_submit)
    assert start(f)['state'] == 'running'
    assert f.runtime.executions.budget_accounting(eid)['unsettled_reservation']


def test_repeated_request_is_atomic_before_scheduler_binding_is_written(tmp_path, monkeypatch):
    f = continuation(tmp_path, monkeypatch)
    request = dict(name='repeat-before-binding', operation_id='science.object.review.v1',
        inputs=[dict(port='subject', artifact_names=['observe.output'])], instruction='Exact review.')
    with monkeypatch.context() as patch:
        patch.setattr(f.root.facade, '_bind_target', lambda *args: None)
        # Invoke schedule directly through the real routing helper; skip status
        # since this intentionally simulates the pre-binding crash window.
        patch.setattr(f.root.facade, 'run_status', lambda **kwargs: {'state': 'queued'})
        first = f.root.call_tool('operation_invoke', request)
    second = f.root.call_tool('operation_invoke', request)
    rows = f.runs.list(instance_id=f.instance.instance_id)
    matching = [r for r in rows if r.operation_id == 'science.object.review.v1']
    assert len(matching) == 1
    assert f.runtime.scheduler_bindings.resolve(instance=f.instance.instance_id, namespace='run',
        name=request['name']) == matching[0].run_id
    assert first['result']['state'] == second['result']['state'] == 'queued'


def test_typed_rejection_crosses_ssh_and_command_identity_translation(tmp_path, monkeypatch):
    import subprocess
    from tcad_artifact.ssh_transport import SSHTCADTransport
    from tcad_artifact import command_adapter as module
    from tcad_artifact.command_adapter import CommandAdapterConfig, CommandTCADExecutorAdapter
    from scidiscovery.artifact_agent.schema.execution import LocalFileDescriptor
    local, remote = 'a'*64, 'b'*64
    class Remote:
        def rpc(self, request):
            assert request['params']['arguments']['submission']['sha256'] == remote
            return {'id': request['id'], 'error': {'code': -32000, 'message': 'capacity full',
                'data': {'submission_rejection': {'state': 'not_created', 'submission_sha256': remote,
                    'reason': 'capacity_exhausted'}}}}
    transport = SSHTCADTransport(remote=Remote(), local_result_root=tmp_path / 'transport')
    monkeypatch.setattr(transport, '_read_marker', lambda *_: {'remote_submission': {'sha256': remote}})
    def command(*args, input, **kwargs):
        request = json.loads(input)
        try:
            transport.handle(request['operation'], request['payload'])
        except SubmissionNotCreated as error:
            response = {'schema_version': 1, 'operation': request['operation'], 'ok': False,
                'error': str(error), 'payload': {}, 'submission_rejection': error.wire()}
        return subprocess.CompletedProcess(args, 0, json.dumps(response).encode(), b'')
    monkeypatch.setattr(module, 'run_bounded', command)
    adapter = CommandTCADExecutorAdapter(CommandAdapterConfig(executable='/bin/true'), local_result_root=tmp_path / 'results')
    descriptor = LocalFileDescriptor(name='submission', local_path=str(tmp_path / 'local.json'),
        sha256=local, size_bytes=0, media_type='application/json')
    with pytest.raises(SubmissionNotCreated) as caught:
        adapter.submit(descriptor)
    assert caught.value.submission_sha256 == local and caught.value.reason == 'capacity_exhausted'


def test_unbound_development_bridge_is_definite_pretransport_rejection(tmp_path):
    from tcad_artifact.debug_adapter import TCADDevelopmentDebugBridge
    from scidiscovery.artifact_agent.schema.execution import LocalFileDescriptor
    bridge = TCADDevelopmentDebugBridge(SimpleNamespace(submit=lambda _: pytest.fail('must not reach transport')))
    descriptor = LocalFileDescriptor(name='submission', local_path=str(tmp_path / 'local.json'),
        sha256='a'*64, size_bytes=0, media_type='application/json')
    with pytest.raises(SubmissionNotCreated) as caught:
        bridge.submit(descriptor)
    assert caught.value.reason == 'local_validation_failed'


def test_legacy_diagnostic_never_gains_retirement_from_adapter_upgrade(tmp_path, monkeypatch):
    from tests.operations.test_debug_readonly_status import fixture
    service, context, path = fixture(tmp_path, reservation=True)
    before = path.read_bytes()
    monkeypatch.setattr(service.adapter, 'retire_submission', lambda _: pytest.fail('legacy request not proven fenced'), raising=False)
    inputs = (SimpleNamespace(port_name='research_objective', artifact_ref=context.input_ref('')),)
    with pytest.raises(DiagnosticError):
        service.experiment_activity({'private-run'}, inputs, cancel=True, name='init')
    assert path.read_bytes() == before


def test_root_diagnostic_lookup_does_not_borrow_another_tasks_submission(tmp_path):
    from tests.operations.test_debug_readonly_status import fixture
    service, context, path = fixture(tmp_path, reservation=True)
    inputs = (SimpleNamespace(port_name='research_objective', artifact_ref=context.input_ref('')),)
    assert service.experiment_diagnostic_status({'other-run'}, inputs, name='init') == {
        'name': 'init', 'state': 'not_created', 'submission': 'not_submitted'}


def test_submission_proof_stays_inside_private_control_protocol(tmp_path, monkeypatch):
    from tcad_artifact.execution_control import TCADExecutionRouter
    from tcad_artifact.execution_adapter import TCADExecutorAdapter
    from scidiscovery.artifact_agent.schema.execution import LocalFileDescriptor
    from scidiscovery.plugin_runtime.transport import rpc_error
    descriptor = LocalFileDescriptor(name='submission', local_path=str(tmp_path / 'job.json'),
        sha256='a'*64, size_bytes=0, media_type='application/json')
    def reject(**kwargs):
        raise SubmissionNotCreated(descriptor.sha256, 'capacity_exhausted')
    router = TCADExecutionRouter(SimpleNamespace(tcad_submit_retirable=reject))
    value = router.call_tool('tcad_submit_retirable', {'submission': descriptor.model_dump(exclude={'schema_version'})})
    adapter = object.__new__(TCADExecutorAdapter)
    monkeypatch.setattr(adapter, '_call', lambda *_: value)
    with pytest.raises(SubmissionNotCreated) as caught:
        adapter.submit(descriptor)
    public = rpc_error(1, caught.value)
    assert descriptor.sha256 not in json.dumps(public)
    assert public['error']['data']['diagnostics'][0]['code'] == 'execution_capacity_busy'


def test_existing_database_migrates_operation_mutex_without_losing_runs(tmp_path, monkeypatch):
    f = continuation(tmp_path, monkeypatch)
    with f.runs._connect() as connection:
        connection.execute("CREATE UNIQUE INDEX one_active_run_per_operation ON runs(operation_digest) WHERE state IN ('queued','running')")
    ids = {row.run_id for row in f.runs.list(instance_id=f.instance.instance_id)}
    f.runs._initialize()
    with f.runs._connect() as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='one_active_run_per_operation'").fetchone()
    assert {row.run_id for row in f.runs.list(instance_id=f.instance.instance_id)} == ids


def test_administrator_can_enable_legacy_diagnostic_fencing_without_refunding(tmp_path, monkeypatch):
    import sqlite3
    from tests.operations.test_debug_readonly_status import fixture
    from scidiscovery.artifact_agent.interfaces.submission_recovery import authorize_legacy_diagnostic_retirement
    service, context, path = fixture(tmp_path, reservation=True)
    (tmp_path / 'database').mkdir()
    with sqlite3.connect(tmp_path / 'database/runs.sqlite3') as db:
        db.execute('CREATE TABLE runs(run_id TEXT)')
        db.execute('INSERT INTO runs VALUES (?)', ('private-run',))
    before = path.read_bytes()
    options = dict(run_id='private-run', name='init', reason='Verified old submitters stopped.')
    with pytest.raises(ValueError):
        authorize_legacy_diagnostic_retirement(tmp_path, path, confirm_old_submitters_stopped=False, **options)
    assert path.read_bytes() == before
    result = authorize_legacy_diagnostic_retirement(tmp_path, path, confirm_old_submitters_stopped=True, **options)
    assert result == {'state': 'remote_retirement_required', 'budget_released': False}
    retained = json.loads(path.read_bytes())['reservations']['init']
    assert retained['retirement_supported'] is True
    assert retained['administrator_recovery']['submission_sha256'] == 'a'*64
    monkeypatch.setattr(service.adapter, 'retire_submission', lambda _: None, raising=False)
    inputs = (SimpleNamespace(port_name='research_objective', artifact_ref=context.input_ref('')),)
    service.experiment_activity({'private-run'}, inputs, cancel=True, name='init')
    after = json.loads(path.read_bytes())
    assert after['runs']['init']['submission_absent'] is True
    assert after['reservations']['init']['administrator_recovery'] == retained['administrator_recovery']


def test_limited_report_survives_unavailable_unknown_lookup(tmp_path, monkeypatch):
    f = continuation(tmp_path, monkeypatch)
    service = _service(f)
    def unavailable(*args):
        raise TimeoutError('transport offline')
    monkeypatch.setattr(service.adapter, 'submit', unavailable)
    monkeypatch.setattr(service.adapter, 'lookup_submission', unavailable)
    with pytest.raises(DiagnosticError):
        _debug(f, 'uncertain-offline')
    reserved = _ledger(service)['reserved_wall_seconds']
    (f.workspace / 'output/result.json').write_text(json.dumps({'schema_version': 1, 'payload': {
        'outcome': 'inconclusive', 'summary': 'Transport unavailable.',
        'limitations': ['The original diagnostic submission is unresolved; no numerical evidence is claimed.'],
        'remaining_question': '', 'adopted_stages': []}}))
    assert f.worker.call_tool('worker_submit_result', {})['state'] == 'completed'
    assert _ledger(service)['reserved_wall_seconds'] == reserved
    with pytest.raises(DiagnosticError):
        f.root.call_tool('run_diagnostic', {'name': 'continuation', 'diagnostic_name': 'uncertain-offline'}, surface='execution')
    assert f.runs.submission_attention(f.runs.status(f.run_id))['code'] == 'experiment_diagnostic_submission_unknown'


def test_root_preserves_recovered_identity_before_failed_status_and_settles_usage(tmp_path, monkeypatch):
    from tests.operations.test_debug_readonly_status import fixture
    service, context, path = fixture(tmp_path, reservation=True, lookup=('recovered', 'running'))
    ledger = json.loads(path.read_bytes())
    ledger['reserved_wall_seconds'] = 5
    ledger['reservations']['init']['record']['reserved_wall_seconds'] = 5
    path.write_text(json.dumps(ledger))
    inputs = (SimpleNamespace(port_name='research_objective', artifact_ref=context.input_ref('')),)
    def unavailable(_):
        raise ConnectionError('temporary outage after lookup')
    monkeypatch.setattr(service.adapter, 'status', unavailable)
    with pytest.raises(DiagnosticError):
        service.experiment_diagnostic_status({'private-run'}, inputs, name='init')
    persisted = json.loads(path.read_bytes())
    assert persisted['runs']['init']['external_run_id'] == 'recovered'
    monkeypatch.setattr(service.adapter, 'lookup_submission', lambda _: pytest.fail('must retain exact recovered binding'))
    monkeypatch.setattr(service.adapter, 'status_details', lambda _: {
        'state': 'succeeded', 'consumed_budget': {'wall_time_seconds': 1}}, raising=False)
    result = service.experiment_diagnostic_status({'private-run'}, inputs, name='init')
    assert result['state'] == 'succeeded' and result['allowance_reserved'] is False
    assert json.loads(path.read_bytes())['reserved_wall_seconds'] == 1
