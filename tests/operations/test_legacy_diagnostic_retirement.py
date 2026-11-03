"""Exact legacy owner recovery through Root/Worker routes; no live submitters."""
import fcntl
import json
import shutil

import pytest

from scidiscovery.artifact_agent.interfaces.submission_recovery import authorize_named_diagnostic_retirement
from scidiscovery.artifact_agent.service.engineering_diagnostics import atomic_json
from scidiscovery.operation_contract import DiagnosticError
from tests.operations.test_experiment_continuation import continuation
from tests.operations.test_preparation_and_diagnostic_takeover import root_for
from tests.operations.test_review_delivery_and_debug_scope import debug, debug_service


def legacy(tmp_path, monkeypatch, *, complete=True):
    f = continuation(tmp_path, monkeypatch)
    service = debug_service(f)
    # Match the installed plugin's configured location; the older fixture uses
    # a deliberately arbitrary exchange root.
    configured = f.runtime.state_root / 'local-tcad-debug'
    shutil.copytree(service.exchange_root, configured)
    service.exchange_root = configured
    def lost(_):
        raise TimeoutError('old submit acknowledgment lost')
    monkeypatch.setattr(service.adapter, 'submit', lost)
    monkeypatch.setattr(service.adapter, 'lookup_submission', lambda _: None)
    monkeypatch.setattr(service.adapter, 'retire_submission', lambda _: None, raising=False)
    with pytest.raises(DiagnosticError):
        debug(f, 'ga_init_110')
    path = next((service.exchange_root / 'budgets').glob('*.json'))
    ledger = json.loads(path.read_bytes())
    ledger['reservations']['ga_init_110'].pop('retirement_supported')
    atomic_json(path, ledger)
    if complete:
        complete_blocked(f)
    return f, service, path


def complete_blocked(f):
    (f.workspace / 'output/result.json').write_text(json.dumps({'schema_version': 1, 'payload': {
        'outcome': 'blocked', 'summary': 'Old diagnostic unresolved.',
        'limitations': ['Unknown submission; no numerical claim.'],
        'remaining_question': '', 'adopted_stages': []}}))
    assert f.worker.call_tool('worker_submit_result', {})['state'] == 'completed'


def authorize(f, **kwargs):
    return authorize_named_diagnostic_retirement(f.runtime.state_root,
        instance=kwargs.pop('instance', f.instance.name), run_name=kwargs.pop('run_name', 'continuation'),
        name='ga_init_110', confirm_old_submitters_stopped=kwargs.pop('confirmed', True),
        reason='Administrator stopped all old local and remote submitters and deployed the fenced runner.', **kwargs)


def request(action='status'):
    return {'name': 'continuation', 'diagnostic_name': 'ga_init_110', 'action': action}


def test_legacy_recovery_named_entry_preserves_report_and_releases_once(tmp_path, monkeypatch):
    f, service, path = legacy(tmp_path, monkeypatch)
    root = root_for(f)
    report = f.runs.status(f.run_id).output_ref
    original = f.runtime.artifacts.read(report)
    status = root.call_tool('run_diagnostic', request(), surface='execution')
    recovery = status['recovery']
    assert recovery['code'] == 'tcad_debug_legacy_retirement_confirmation_required'
    assert recovery['owner_name'] == 'continuation' and recovery['diagnostic_name'] == 'ga_init_110'
    assert '--run-name' in recovery['administrator']['arguments']
    assert str(path) not in json.dumps(status) and f.run_id not in json.dumps(status)
    before = path.read_bytes()
    with pytest.raises(DiagnosticError) as caught:
        root.call_tool('run_diagnostic', request('cancel'), surface='execution')
    assert caught.value.details[0]['code'] == recovery['code']
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match='Confirm'):
        authorize(f, confirmed=False)
    with pytest.raises(ValueError, match='active instance'):
        authorize(f, instance='foreign')
    with pytest.raises(ValueError, match='active instance'):
        authorize(f, run_name='foreign')
    with path.with_suffix('.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        with pytest.raises(RuntimeError, match='owner still holds'):
            authorize(f)
    assert path.read_bytes() == before
    assert authorize(f)['budget_released'] is False
    enabled = json.loads(path.read_bytes())
    assert enabled['reserved_wall_seconds'] == json.loads(before)['reserved_wall_seconds']
    calls = []
    monkeypatch.setattr(service.adapter, 'retire_submission', lambda submission: calls.append(submission.sha256))
    cancelled = root.call_tool('run_diagnostic', request('cancel'), surface='execution')
    assert cancelled['state'] == 'cancelled' and not cancelled['allowance_reserved']
    settled = path.read_bytes()
    assert root.call_tool('run_diagnostic', request('cancel'), surface='execution') == cancelled
    assert path.read_bytes() == settled and len(calls) == 1
    assert json.loads(settled)['runs']['ga_init_110']['actual_wall_seconds'] == 0
    assert f.runs.submission_attention(f.runs.status(f.run_id)) is None
    assert f.runtime.artifacts.read(report) == original
    assert f.runs.status(f.run_id).state == 'completed'


@pytest.mark.parametrize('failure,code', [
    ('protocol', 'tcad_debug_retirement_protocol_unavailable'),
    ('busy', 'tcad_debug_retirement_busy'), ('failed', 'tcad_debug_retirement_failed')])
def test_no_fence_proof_preserves_exact_reservation(tmp_path, monkeypatch, failure, code):
    f, service, path = legacy(tmp_path, monkeypatch)
    authorize(f)
    def unavailable(*_):
        raise RuntimeError('Submission is still in progress; retry abandon/cancel later.' if failure == 'busy' else 'backend unavailable')
    monkeypatch.setattr(service.adapter, 'validate_submission_protocol' if failure == 'protocol' else 'retire_submission', unavailable, raising=False)
    root = root_for(f)
    if failure == 'protocol':
        assert root.call_tool('run_diagnostic', request(), surface='execution')['recovery']['code'] == code
    before = path.read_bytes()
    with pytest.raises(DiagnosticError) as caught:
        root.call_tool('run_diagnostic', request('cancel'), surface='execution')
    assert caught.value.details[0]['code'] == code
    assert path.read_bytes() == before
    assert f.runs.submission_attention(f.runs.status(f.run_id)) is not None


@pytest.mark.parametrize('state', ['running', 'succeeded'])
def test_recovery_finds_original_job_keeps_collection_and_settles_usage(tmp_path, monkeypatch, state):
    from tcad_artifact.debug_contract import CollectedTCADDebugFile, CollectedTCADDebugRun
    f, service, path = legacy(tmp_path, monkeypatch)
    authorize(f)
    root = root_for(f)
    retired, cancelled = [], []
    monkeypatch.setattr(service.adapter, 'retire_submission', lambda submission:
        retired.append(submission.sha256) or ('original-job', state))
    monkeypatch.setattr(service.adapter, 'status', lambda _: state)
    monkeypatch.setattr(service.adapter, 'cancel', lambda job: cancelled.append(job) or 'cancelled', raising=False)
    monkeypatch.setattr(service.adapter, 'status_details', lambda _: {
        'state': 'cancelled' if cancelled else state, 'consumed_budget': {'wall_time_seconds': 2}}, raising=False)
    result = root.call_tool('run_diagnostic', request('cancel'), surface='execution')
    assert result['state'] == ('cancelled' if state == 'running' else 'succeeded')
    assert len(retired) == 1 and cancelled == (['original-job'] if state == 'running' else [])
    ledger = json.loads(path.read_bytes())
    assert ledger['runs']['ga_init_110']['external_run_id'] == 'original-job'
    assert ledger['runs']['ga_init_110']['actual_wall_seconds'] == 2
    assert not ledger['runs']['ga_init_110'].get('submission_absent')
    assert f.runs.submission_attention(f.runs.status(f.run_id))['code'] == 'experiment_diagnostic_results_pending'
    monkeypatch.setattr(service.adapter, 'collect_with_budget', lambda *a, **k: CollectedTCADDebugRun(
        result['state'], 0, 'complete', 'Original result retained.', '',
        (CollectedTCADDebugFile('debug.log.txt', 'text/plain', b'original output\n'),), timing={'elapsed_seconds': 2}), raising=False)
    collected = root.call_tool('run_diagnostic', request('collect'), surface='execution')
    assert collected['state'] == 'collected'
    assert f.runs.submission_attention(f.runs.status(f.run_id)) is None


def test_new_task_points_root_to_old_owner_then_submits_new_request_once(tmp_path, monkeypatch):
    from pathlib import Path
    from scidiscovery.artifact_agent.interfaces.mcp_local_worker import LocalWorkerMCPRouter
    from scidiscovery.artifact_agent.service.experiment_execution import bind_experiment_services
    f, service, path = legacy(tmp_path, monkeypatch)
    original_owner = f.run_id
    f.root.call_tool('operation_invoke', {**f.invocation, 'name': 'replacement'})
    run_id = f.runtime.scheduler_bindings.resolve(instance=f.instance.instance_id, namespace='run', name='replacement')
    f.runs.worker_connections.attach(run_id=run_id, platform_session='fixture', thread_id='replacement')
    compiled = f.catalog.operation('science.experiment.v1')
    services = bind_experiment_services(f.runs, compiled, dict(f.coordinator.worker_services), coordinator=f.coordinator)
    f.worker = LocalWorkerMCPRouter(f.runs, operation_id=compiled.spec.operation_id,
        operation_digest=compiled.digest, tool_services=services)
    f.workspace = Path(f.worker.call_tool('worker_open_assignment', {})['workspace_path'])
    f.run_id = run_id
    with pytest.raises(DiagnosticError) as caught:
        debug(f, 'ga_init_111')
    assert "name='continuation'" in str(caught.value) and "diagnostic_name='ga_init_110'" in str(caught.value)
    assert original_owner not in str(caught.value) and str(path) not in str(caught.value)
    assert 'ga_init_111' not in json.loads(path.read_bytes())['reservations']
    root = root_for(f)
    assert root.call_tool('run_diagnostic', {**request('cancel'), 'name': 'replacement'}, surface='execution')['state'] == 'not_created'
    authorize(f)
    root.call_tool('run_diagnostic', request('cancel'), surface='execution')
    calls = []
    monkeypatch.setattr(service.adapter, 'submit', lambda _: calls.append('new') or ('new-job', 'succeeded'))
    monkeypatch.setattr(service.adapter, 'status_details', lambda _: {'state': 'succeeded'}, raising=False)
    debug(f, 'ga_init_111')
    debug(f, 'ga_init_111')
    assert calls == ['new']


def test_named_recovery_rejects_foreign_instance_binding(tmp_path, monkeypatch):
    import sqlite3
    f, _, _ = legacy(tmp_path, monkeypatch)
    foreign = f.runtime.scheduler_bindings.create_instance(name='foreign', title='Foreign', objective='Foreign objective')
    # A stale or corrupt binding must not promote a different instance's record.
    with sqlite3.connect(f.runtime.scheduler_bindings.database_path) as db:
        db.execute('INSERT INTO scheduler_bindings SELECT ?,namespace,name,logical_name,revision,object_id,request_fingerprint,created_at FROM scheduler_bindings WHERE instance=? AND namespace=? AND name=?',
            (foreign.instance_id, f.instance.instance_id, 'run', 'continuation'))
    with pytest.raises(ValueError, match='does not belong'):
        authorize(f, instance='foreign')


def test_named_administrator_cli_returns_exact_root_route(tmp_path, monkeypatch, capsys):
    from scidiscovery.artifact_agent.interfaces.submission_recovery import main
    f, _, _ = legacy(tmp_path, monkeypatch)
    capsys.readouterr()
    main(['--state-root', str(f.runtime.state_root), '--instance', f.instance.name,
        '--run-name', 'continuation', '--diagnostic-name', 'ga_init_110',
        '--confirm-old-submitters-stopped', '--reason', 'Administrator stopped old submitter processes.'])
    result = json.loads(capsys.readouterr().out)
    assert result['budget_released'] is False
    assert result['next_request']['arguments'] == request('cancel')


def test_failed_original_owner_completed_recovery_uses_named_cli_exactly(tmp_path, monkeypatch, capsys):
    from pathlib import Path
    from scidiscovery.artifact_agent.interfaces.mcp_local_worker import LocalWorkerMCPRouter
    from scidiscovery.artifact_agent.interfaces.submission_recovery import main
    from scidiscovery.artifact_agent.service.experiment_execution import bind_experiment_services
    f, service, path = legacy(tmp_path, monkeypatch, complete=False)
    original_id = f.run_id
    original = f.runs.status(original_id)
    f.runs.record_failure(original_id, reason='Original Worker timed out after losing the acknowledgment.',
        expected_state='running', expected_last_activity_at=original.last_activity_at)
    invoked = f.root.call_tool('operation_invoke', {**f.invocation, 'name': 'recovered', 'draft_from': 'continuation'})
    assert invoked['result']['state'] == 'queued'
    recovered_id = f.runtime.scheduler_bindings.resolve(instance=f.instance.instance_id, namespace='run', name='recovered')
    f.runs.worker_connections.attach(run_id=recovered_id, platform_session='fixture', thread_id='recovered')
    compiled = f.catalog.operation('science.experiment.v1')
    services = bind_experiment_services(f.runs, compiled, dict(f.coordinator.worker_services), coordinator=f.coordinator)
    f.worker = LocalWorkerMCPRouter(f.runs, operation_id=compiled.spec.operation_id,
        operation_digest=compiled.digest, tool_services=services)
    f.workspace = Path(f.worker.call_tool('worker_open_assignment', {})['workspace_path'])
    f.run_id = recovered_id
    complete_blocked(f)
    report = f.runs.status(recovered_id).output_ref
    original_report = f.runtime.artifacts.read(report)
    root = root_for(f)
    observed = root.call_tool('run_diagnostic', {**request(), 'name': 'recovered'}, surface='execution')
    recovery = observed['recovery']
    assert recovery['owner_name'] == 'continuation'
    assert recovery['request']['arguments'] == request('cancel')
    assert f.runs.submission_attention(f.runs.status(recovered_id)) is not None
    # The completed successor is a route to the original; it never becomes the
    # owner in the administrator audit or exact reservation.
    with pytest.raises(ValueError, match='no unique diagnostic reservation'):
        authorize(f, run_name='recovered')
    before = json.loads(path.read_bytes())
    capsys.readouterr()
    main(['--state-root', str(f.runtime.state_root), *recovery['administrator']['arguments'],
        '--confirm-old-submitters-stopped', '--reason', 'Administrator stopped the old local and remote submitters.'])
    enabled = json.loads(capsys.readouterr().out)
    assert enabled['budget_released'] is False and enabled['owner_name'] == 'continuation'
    ledger = json.loads(path.read_bytes())
    assert ledger['reserved_wall_seconds'] == before['reserved_wall_seconds']
    assert ledger['reservations']['ga_init_110']['administrator_recovery']['run_id'] == original_id
    calls = []
    monkeypatch.setattr(service.adapter, 'retire_submission', lambda submission: calls.append(submission.sha256))
    result = root.call_tool('run_diagnostic', {**request('cancel'), 'name': 'recovered'}, surface='execution')
    assert result['state'] == 'cancelled' and not result['allowance_reserved']
    settled = path.read_bytes()
    root.call_tool('run_diagnostic', {**request('cancel'), 'name': 'recovered'}, surface='execution')
    assert path.read_bytes() == settled and len(calls) == 1
    assert f.runs.submission_attention(f.runs.status(original_id)) is None
    assert f.runs.submission_attention(f.runs.status(recovered_id)) is None
    assert f.runs.status(original_id).state == 'failed'
    assert f.runs.status(recovered_id).state == 'completed'
    assert f.runtime.artifacts.read(report) == original_report


def test_named_recovery_rejects_active_original_owner_without_mutation(tmp_path, monkeypatch):
    f, service, path = legacy(tmp_path, monkeypatch, complete=False)
    assert f.runs.status(f.run_id).state == 'running'
    before = path.read_bytes()
    monkeypatch.setattr(service.adapter, 'retire_submission', lambda _: pytest.fail('Active owner cannot gain a fence'))
    with pytest.raises(ValueError, match='Run is still active'):
        authorize(f)
    assert path.read_bytes() == before
    reservation = json.loads(before)['reservations']['ga_init_110']
    assert 'administrator_recovery' not in reservation and 'retirement_supported' not in reservation
