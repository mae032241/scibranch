"""Persistent preparation readiness and late diagnostic delivery through real routes."""
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from scidiscovery.artifact_agent.interfaces.mcp_root import RootMCPRouter, RootToolFacade
from scidiscovery.artifact_agent.interfaces.mcp_local_worker import LocalWorkerMCPRouter, WorkerToolError
from scidiscovery.artifact_agent.service.run_records import RunStateConflict
from scidiscovery.operation_contract import DiagnosticError
from tests.operations.test_experiment_continuation import continuation


def root_for(f):
    return RootMCPRouter(RootToolFacade(f.runtime.artifacts, f.runtime.intake, runs=f.runs,
        approvals=f.runtime.approvals, executions=f.runtime.executions,
        bindings=f.runtime.scheduler_bindings, instance=f.instance.instance_id, operation_catalog=f.catalog))


def review_request(f, name):
    return {'name': name, 'operation_id': 'science.object.review.v1',
        'inputs': [{'port': 'subject', 'artifact_names': ['observe.output']}], 'instruction': 'Review the exact report.'}


def test_duplicate_invoke_cannot_dispatch_or_open_during_preparation(tmp_path, monkeypatch):
    f = continuation(tmp_path, monkeypatch)
    original = f.runs.backend.prepare
    started, release = threading.Event(), threading.Event()
    def paused(**values):
        started.set()
        assert release.wait(15)
        return original(**values)
    monkeypatch.setattr(f.runs.backend, 'prepare', paused)
    request = review_request(f, 'paused-review')
    other = root_for(f)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(f.root.call_tool, 'operation_invoke', request)
        assert started.wait(15)
        try:
            duplicate = other.call_tool('operation_invoke', request)
            assert duplicate['result']['state'] == 'queued'
            assert 'dispatch' not in duplicate['result']
            assert 'preparation' in duplicate['result']
            run_id = f.runtime.scheduler_bindings.resolve(instance=f.instance.instance_id,
                namespace='run', name='paused-review')
            assert f.runs.status(run_id).workspace_ready is False
            f.runs.worker_connections.attach(run_id=run_id, platform_session='review-fixture', thread_id='reviewer')
            worker = LocalWorkerMCPRouter(f.runs, operation_id='science.object.review.v1',
                operation_digest=f.catalog.operation('science.object.review.v1').digest,
                run_id=run_id, trusted_caller=('review-fixture', 'reviewer'))
            for _ in range(2):
                with pytest.raises(WorkerToolError) as caught:
                    worker.call_tool('worker_open_assignment', {})
                assert caught.value.details[0]['code'] == 'run_preparation_pending'
                assert caught.value.details[0]['repairable'] is True
            assert f.runs.status(run_id).state == 'queued'
        finally:
            release.set()
        created = pending.result(timeout=15)
    assert 'dispatch' in created['result']
    assert f.runs.status(run_id).workspace_ready is True
    assert worker.call_tool('worker_open_assignment', {})['state'] == 'opened'
    assert f.runs.status(run_id).state == 'running'


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='requires OS crash semantics')
@pytest.mark.parametrize("published_binding", [True, False])
def test_process_death_after_workspace_creation_recovers_frozen_run(tmp_path, monkeypatch, published_binding):
    f = continuation(tmp_path, monkeypatch)
    original = f.runs.backend.prepare
    request = review_request(f, 'crashed-review')
    child = os.fork()
    if child == 0:
        def crash(**values):
            original(**values)
            if not published_binding:
                f.runs.backend._binding_path(values['run_id']).unlink()
            os._exit(23)  # Real process exit: no finally, no record_failure.
        f.runs.backend.prepare = crash
        f.root.call_tool('operation_invoke', request)
        os._exit(99)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 23
    pending = next(r for r in f.runs.list(instance_id=f.instance.instance_id)
        if r.output_binding_name == 'crashed-review.output')
    assert pending.state == 'queued' and pending.workspace_ready is False
    before = pending.inputs
    # New service process view recovers an unready record, not a fresh request.
    from scidiscovery.artifact_agent.service.runs import RunService
    restarted = RunService(artifacts=f.runs.artifacts, database_path=f.runs.database_path,
        service_actor=f.runs.service_actor, operation_catalog=f.catalog, backend=f.runs.backend,
        scheduler_bindings=f.runtime.scheduler_bindings)
    assert restarted.prepare_queued(pending.run_id)
    replay = f.root.call_tool('operation_invoke', request)
    assert 'dispatch' in replay['result']
    recovered = f.runs.status(pending.run_id)
    assert recovered.inputs == before and recovered.workspace_ready and recovered.state == 'queued'
    assert sum(r.output_binding_name == 'crashed-review.output'
        for r in f.runs.list(instance_id=f.instance.instance_id)) == 1
    restarted.worker_connections.attach(run_id=pending.run_id, platform_session='restart', thread_id='reviewer')
    worker = LocalWorkerMCPRouter(restarted, operation_id=pending.operation_id,
        operation_digest=pending.operation_digest, run_id=pending.run_id, trusted_caller=('restart', 'reviewer'))
    worker.call_tool('worker_open_assignment', {})
    assert restarted.status(pending.run_id).state == 'running'


def late_diagnostic(f, monkeypatch, name="late"):
    service = f.coordinator.worker_services['tcad_artifact:tcad.development_debug']
    monkeypatch.setattr(service.adapter, 'submit', lambda _: (_ for _ in ()).throw(TimeoutError('lost ack')))
    monkeypatch.setattr(service.adapter, 'lookup_submission', lambda _: None)
    with pytest.raises(DiagnosticError):
        f.worker.call_tool('worker_experiment_debug', {'implementation': f.alias, 'name': name, 'mode': 'preflight'})
    (f.workspace / 'output/result.json').write_text(json.dumps({'schema_version': 1, 'payload': {
        'outcome': 'blocked', 'summary': 'Diagnostic unresolved.', 'limitations': ['Unknown submission; no numerical claim.'],
        'remaining_question': '', 'adopted_stages': []}}))
    assert f.worker.call_tool('worker_submit_result', {})['state'] == 'completed'
    monkeypatch.setattr(service.adapter, 'lookup_submission', lambda _: ('late-original-job', 'succeeded'))
    monkeypatch.setattr(service.adapter, 'status_details', lambda _: {'state': 'succeeded',
        'consumed_budget': {'wall_time_seconds': 1}}, raising=False)
    return service


@pytest.mark.parametrize('fail_first', [None, 'collection', 'publication'])
def test_root_collects_late_result_after_blocked_completion_and_retries_idempotently(tmp_path, monkeypatch, fail_first):
    from tcad_artifact.debug_contract import CollectedTCADDebugFile, CollectedTCADDebugRun
    f = continuation(tmp_path, monkeypatch)
    service = late_diagnostic(f, monkeypatch)
    report = f.runs.status(f.run_id).output_ref
    report_bytes = f.runtime.artifacts.read(report)
    calls = []
    def collect(job, *, context, limits):
        calls.append(job)
        if fail_first == 'collection' and len(calls) == 1:
            raise TimeoutError('collection interrupted')
        return CollectedTCADDebugRun('succeeded', 0, 'complete', 'Original diagnostic completed.', '',
            (CollectedTCADDebugFile('debug.log.txt', 'text/plain', b'original solver log\n'),), timing={'elapsed_seconds': 1})
    monkeypatch.setattr(service.adapter, 'collect_with_budget', collect, raising=False)
    # A recreated Root facade is not dependent on the original Worker's lifetime.
    root = root_for(f)
    request = {'name': 'continuation', 'diagnostic_name': 'late'}
    observed = root.call_tool('run_diagnostic', request, surface='execution')
    assert observed['state'] == 'succeeded'
    assert f.runs.submission_attention(f.runs.status(f.run_id))['code'] == 'experiment_diagnostic_results_pending'
    if fail_first == 'publication':
        publish = f.coordinator._publish_diagnostic_delivery
        failed = []
        def fail_once(*args):
            if not failed:
                failed.append(True)
                raise OSError('CAS temporary failure')
            return publish(*args)
        monkeypatch.setattr(f.coordinator, '_publish_diagnostic_delivery', fail_once)
    if fail_first:
        with pytest.raises((DiagnosticError, OSError)):
            root.call_tool('run_diagnostic', {**request, 'action': 'collect'}, surface='execution')
        assert f.runs.status(f.run_id).state == 'completed'
        assert f.runs.submission_attention(f.runs.status(f.run_id)) is not None
    delivered = root.call_tool('run_diagnostic', {**request, 'action': 'collect'}, surface='execution')
    assert delivered['state'] == 'collected' and delivered['delivery']['file_count'] >= 2
    repeat = root.call_tool('run_diagnostic', {**request, 'action': 'collect'}, surface='execution')
    assert repeat['delivery'] == delivered['delivery']
    assert len(calls) == (2 if fail_first == 'collection' else 1)
    assert f.runtime.artifacts.read(report) == report_bytes
    manifest_page = root.call_tool('run_diagnostic', {**request, 'action': 'read'}, surface='execution')
    manifest = json.loads(manifest_page['content'])
    log_name = next(item['file_name'] for item in manifest['files'] if item['media_type'] == 'text/plain')
    read = root.call_tool('run_diagnostic', {**request, 'action': 'read', 'file_name': log_name, 'max_bytes': 8}, surface='execution')
    assert read['content'] == 'original' and read['next_offset'] == 8
    tail = root.call_tool('run_diagnostic', {**request, 'action': 'read', 'file_name': log_name, 'offset': 8}, surface='execution')
    assert tail['content'] == ' solver log\n'
    assert f.runs.submission_attention(f.runs.status(f.run_id)) is None
    # No submission happens on a nonexistent name; exact subject alone grants no takeover.
    assert root.call_tool('run_diagnostic', {**request, 'diagnostic_name': 'missing', 'action': 'collect'},
        surface='execution')['state'] == 'not_created'


def test_unrelated_task_cannot_collect_or_read_late_diagnostic(tmp_path, monkeypatch):
    f = continuation(tmp_path, monkeypatch)
    service = late_diagnostic(f, monkeypatch)
    monkeypatch.setattr(service.adapter, 'collect_with_budget', lambda *a, **k: pytest.fail('Unauthorized collection'), raising=False)
    unrelated = {**f.invocation, 'name': 'unrelated'}
    f.root.call_tool('operation_invoke', unrelated)
    for action in ('status', 'collect', 'read', 'cancel'):
        assert f.root.call_tool('run_diagnostic', {'name': 'unrelated', 'diagnostic_name': 'late',
            'action': action}, surface='execution')['state'] == 'not_created'
    assert f.runs.status(f.run_id).state == 'completed'


@pytest.mark.parametrize('unsafe', [b'/var/lib/private-control/credential.txt', b'password=example_credential_123456789'])
@pytest.mark.parametrize('field', ['log', 'summary'])
def test_takeover_public_boundary_withholds_unsafe_originals_without_delivery_deadlock(tmp_path, monkeypatch, unsafe, field):
    from tcad_artifact.debug_contract import CollectedTCADDebugFile, CollectedTCADDebugRun
    f = continuation(tmp_path, monkeypatch)
    service = late_diagnostic(f, monkeypatch)
    calls = []
    log = b'Solver completed; concentration=3e15 cm^-3.\n' + (unsafe if field == 'log' else b'clean log')
    summary = unsafe.decode() if field == 'summary' else 'Clean diagnostic conclusion.'
    def collect(job, *, context, limits):
        calls.append(job)
        return CollectedTCADDebugRun('succeeded', 0, 'complete', summary, '',
            (CollectedTCADDebugFile('debug.log.txt', 'text/plain', log),), timing={'elapsed_seconds': 1})
    monkeypatch.setattr(service.adapter, 'collect_with_budget', collect, raising=False)
    request = {'name': 'continuation', 'diagnostic_name': 'late'}
    delivered = f.root.call_tool('run_diagnostic', {**request, 'action': 'collect'}, surface='execution')
    assert delivered['state'] == 'collected'
    assert delivered['delivery']['restricted_file_count'] >= 1
    assert unsafe.decode() not in json.dumps(delivered)
    assert f.runs.submission_attention(f.runs.status(f.run_id)) is None
    repeated = f.root.call_tool('run_diagnostic', {**request, 'action': 'collect'}, surface='execution')
    assert repeated['delivery'] == delivered['delivery'] and len(calls) == 1
    status = f.root.call_tool('run_diagnostic', request, surface='execution')
    assert unsafe.decode() not in json.dumps(status)
    public = f.root.call_tool('run_diagnostic', {**request, 'action': 'read'}, surface='execution')
    assert unsafe.decode() not in public['content']
    manifest = json.loads(public['content'])
    if field == 'summary':
        assert manifest['restricted_fields'] == ['summary']
        assert 'withheld' in manifest['summary']
    log_entry = next(item for item in manifest['files'] if item['media_type'] == 'text/plain')
    read = f.root.call_tool('run_diagnostic', {**request, 'action': 'read',
        'file_name': log_entry['file_name']}, surface='execution')
    if field == 'log':
        assert log_entry['publication'] == 'restricted' and 'artifact_name' not in log_entry
        assert read['publication'] == 'restricted' and 'content' not in read
    else:
        assert read['content'].encode() == log  # Scientific numeric content is unchanged.
    # The original scientific report and private engineering receipt are untouched.
    receipts = [json.loads((path / 'receipt.json').read_bytes())
        for path in (service.exchange_root / 'budgets/receipts').iterdir()
        if (path / 'receipt.json').exists()]
    retained = next(item for item in receipts if item['proof']['run_id'] == f.run_id
        and item['proof']['run_name'] == 'late')
    assert retained['response']['summary'] == summary
    identity = next(item for item in retained['proof']['files'] if item['media_type'] == 'text/plain')
    private_root = service._receipt_directory(type('Context', (), {'state': {
        'budget_ledger_path': str(next((service.exchange_root / 'budgets').glob('*.json')))}})(), 'late', f.run_id)
    assert (private_root / identity['sha256']).read_bytes() == log


@pytest.mark.parametrize('field', ['log', 'summary'])
def test_legacy_delivery_without_publication_marker_checks_full_object_before_paging(tmp_path, monkeypatch, field):
    from tcad_artifact.debug_contract import CollectedTCADDebugFile, CollectedTCADDebugRun
    from scidiscovery.artifact_agent.schema.artifact import ArtifactRegistration
    from scidiscovery.artifact_agent.schema.refs import ActorRef
    f = continuation(tmp_path, monkeypatch)
    service = late_diagnostic(f, monkeypatch)
    unsafe = 'password=legacy_credential_123456789'
    log = ('clean prefix ' * 100 + (unsafe if field == 'log' else 'clean ending')).encode()
    summary = 'clean prefix ' * 100 + unsafe if field == 'summary' else 'Clean summary.'
    calls = []
    def collect(job, *, context, limits):
        calls.append(job)
        return CollectedTCADDebugRun('succeeded', 0, 'complete', summary, '',
            (CollectedTCADDebugFile('debug.log.txt', 'text/plain', log),), timing={'elapsed_seconds': 1})
    monkeypatch.setattr(service.adapter, 'collect_with_budget', collect, raising=False)
    # Reproduce the older publisher: no publication field, no public-content check.
    def legacy_publish(run, name, receipt, files):
        creator = ActorRef(actor_type='service', actor_id='legacy_diagnostic')
        entries = []
        for ordinal, item in enumerate(files):
            if item['media_type'] != 'text/plain':
                continue
            envelope = f.runtime.artifacts.register(item['path'].read_bytes(), ArtifactRegistration(
                kind='diagnostic_file', schema_id='scidiscovery.opaque.v1', payload_schema_version=1,
                media_type=item['media_type'], creator=creator), idempotency_key='legacy-log-' + str(ordinal))
            alias = 'legacy.log.' + str(ordinal)
            f.runtime.scheduler_bindings.bind(instance=f.instance.instance_id, namespace='artifact', name=alias,
                object_id=envelope.artifact_id)
            entries.append({'file_name': item['relative_path'], 'artifact_name': alias, 'media_type': item['media_type']})
        manifest = {'schema_version': 1, 'summary': summary, 'files': entries}
        envelope = f.runtime.artifacts.register(json.dumps(manifest).encode(), ArtifactRegistration(
            kind='diagnostic_delivery', schema_id='scidiscovery.diagnostic-delivery.v1', payload_schema_version=1,
            media_type='application/json', creator=creator), idempotency_key='legacy-receipt')
        f.runtime.scheduler_bindings.bind(instance=f.instance.instance_id, namespace='artifact', name='legacy.receipt',
            object_id=envelope.artifact_id)
        return {'artifact_name': 'legacy.receipt', 'file_count': len(entries)}
    monkeypatch.setattr(f.coordinator, '_publish_diagnostic_delivery', legacy_publish)
    request = {'name': 'continuation', 'diagnostic_name': 'late'}
    delivered = f.root.call_tool('run_diagnostic', {**request, 'action': 'collect'}, surface='execution')
    repeated = f.root.call_tool('run_diagnostic', {**request, 'action': 'collect'}, surface='execution')
    assert delivered['delivery'] == repeated['delivery'] and len(calls) == 1
    manifest = f.root.call_tool('run_diagnostic', {**request, 'action': 'read'}, surface='execution')
    assert unsafe not in manifest['content']
    visible = json.loads(manifest['content'])
    if field == 'summary':
        assert manifest['publication'] == 'restricted_projection'
        assert visible['restricted_fields'] == ['summary']
    log_name = visible['files'][0]['file_name']
    # An apparently harmless first page must not bypass the unsafe later bytes.
    for offset in (0, 8, 1200):
        page = f.root.call_tool('run_diagnostic', {**request, 'action': 'read',
            'file_name': log_name, 'offset': offset, 'max_bytes': 8}, surface='execution')
        assert unsafe not in json.dumps(page)
        if field == 'log':
            assert page['publication'] == 'restricted' and 'content' not in page
    assert f.runs.submission_attention(f.runs.status(f.run_id)) is None


@pytest.mark.parametrize('field', ['owner', 'diagnostic'])
def test_unsafe_legal_names_use_safe_public_identity_and_do_not_block_delivery(tmp_path, monkeypatch, field):
    from tcad_artifact.debug_contract import CollectedTCADDebugFile, CollectedTCADDebugRun
    f = continuation(tmp_path, monkeypatch)
    unsafe = '/var/lib/private-control' if field == 'owner' else 'sk-' + 'A' * 24
    if field == 'owner':
        # Simulate the legal persisted semantic name without changing exact Run identity.
        with f.runs._connect() as connection:
            connection.execute('UPDATE runs SET output_binding_name=? WHERE run_id=?',
                ('exp:' + unsafe + '.output', f.run_id))
    name = unsafe if field == 'diagnostic' else 'late'
    service = late_diagnostic(f, monkeypatch, name=name)
    calls = []
    def collect(job, *, context, limits):
        calls.append(job)
        return CollectedTCADDebugRun('succeeded', 0, 'complete', 'Clean result.', '',
            (CollectedTCADDebugFile('debug.log.txt', 'text/plain', b'clean numerical log\n'),), timing={'elapsed_seconds': 1})
    monkeypatch.setattr(service.adapter, 'collect_with_budget', collect, raising=False)
    request = {'name': 'continuation', 'diagnostic_name': name}
    result = f.root.call_tool('run_diagnostic', {**request, 'action': 'collect'}, surface='execution')
    assert result['state'] == 'collected' and unsafe not in json.dumps(result)
    assert result['delivery']['artifact_name'].startswith('diagnostic.')
    repeat = f.root.call_tool('run_diagnostic', {**request, 'action': 'collect'}, surface='execution')
    assert repeat['delivery'] == result['delivery'] and len(calls) == 1
    page = f.root.call_tool('run_diagnostic', {**request, 'action': 'read'}, surface='execution')
    assert unsafe not in page['content']
    manifest = json.loads(page['content'])
    assert ('owning_task' if field == 'owner' else 'diagnostic_name') in manifest['restricted_fields']
    assert f.runs.submission_attention(f.runs.status(f.run_id)) is None
