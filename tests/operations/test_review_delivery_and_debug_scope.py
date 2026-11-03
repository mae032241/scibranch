"""Exact late review delivery and diagnostic budget isolation in real Worker routes."""
import json
from pathlib import Path

import pytest

from scidiscovery.operation_contract import DiagnosticError
from tests.operations.test_experiment_continuation import continuation
from tests.operations.test_task_execution_review import review


@pytest.mark.parametrize("prepare_in_current_run", [False, True])
def test_late_reviews_are_readable_in_same_run_without_granting_stale_pass(tmp_path, monkeypatch, prepare_in_current_run):
    f = continuation(tmp_path, monkeypatch, require_execution_review=True)
    if prepare_in_current_run:
        from scidiscovery.artifact_agent.schema.refs import ArtifactRef
        raw = f.runtime.artifacts.read(ArtifactRef.model_validate(f.implementation['artifact_ref']))
        path = f.workspace / 'scratch/experiment.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(json.loads(raw)['implementation']))
        f.alias = f.worker.call_tool('worker_experiment_prepare', {})['implementation']
    request = {"action": "review", "implementation": f.alias}
    first = f.worker.call_tool("worker_experiment_execute", request)
    assert first["review_result"]["state"] == "not_available"
    review(f, "other-subject-review", "observe.output", "pass")
    assert f.worker.call_tool("worker_experiment_execute", request)["review_result"]["state"] == "not_available"
    subject = first["review"]["subject_artifact_name"]
    delivered = []
    for name, verdict in [("first-negative", "revise"), ("then-pass", "pass"), ("later-negative", "blocked")]:
        review(f, name, subject, verdict)
        result = f.worker.call_tool("worker_experiment_execute", request)["review_result"]
        assert result["verdict"] == verdict
        read = f.worker.call_tool("worker_reference_read", result["read_request"])
        assert json.loads(read["fragment"])["verdict"] == verdict
        count = len(f.runs.tool_evidence(f.run_id))
        assert f.worker.call_tool("worker_experiment_execute", request)["review_result"] == result
        assert len(f.runs.tool_evidence(f.run_id)) == count
        file = f.worker.call_tool("worker_reference_read", {**result["read_request"], "delivery": "file"})
        assert json.loads(Path(file["file_path"]).read_bytes())["verdict"] == verdict
        delivered.append(result)
    assert len({item["reference"] for item in delivered}) == 3
    assert json.loads(f.worker.call_tool("worker_reference_read", delivered[1]["read_request"])["fragment"])["verdict"] == "pass"
    with pytest.raises(DiagnosticError) as caught:
        f.worker.call_tool("worker_experiment_execute", {"action": "start", "implementation": f.alias})
    assert caught.value.details[0]["code"] == "experiment_execution_review_not_passed"
    assert f.runs.status(f.run_id).state == "running"
    from tests.operations.test_experiment_continuation import submit
    assert submit(f, [delivered[-1]["reference"]], outcome="blocked")["state"] == "completed"


def test_task_recovery_keeps_diagnostic_limit(tmp_path, monkeypatch):
    from scidiscovery.artifact_agent.interfaces.mcp_local_worker import LocalWorkerMCPRouter
    from scidiscovery.artifact_agent.service.experiment_execution import bind_experiment_services
    f = continuation(tmp_path, monkeypatch)
    service = debug_service(f)
    policy = service.adapter.policy.model_copy(update={"max_runs": 1})
    monkeypatch.setattr(type(service.adapter), "policy", property(lambda _: policy))
    debug(f, 'first')
    status = f.runs.status(f.run_id)
    f.runs.record_failure(f.run_id, reason='fixture interruption', expected_state='running',
        expected_last_activity_at=status.last_activity_at)
    result = f.root.call_tool('operation_invoke', {**f.invocation, 'name':'recovered', 'draft_from':'continuation'})
    assert result['result']['state'] == 'queued'
    recovered_id = f.runtime.scheduler_bindings.resolve(instance=f.instance.instance_id, namespace='run', name='recovered')
    f.runs.worker_connections.attach(run_id=recovered_id, platform_session='recovery-fixture', thread_id='recovered')
    compiled = f.catalog.operation('science.experiment.v1')
    services = bind_experiment_services(f.runs, compiled, dict(f.coordinator.worker_services), coordinator=f.coordinator)
    f.worker = LocalWorkerMCPRouter(f.runs, operation_id=compiled.spec.operation_id, operation_digest=compiled.digest, tool_services=services)
    f.worker.call_tool('worker_open_assignment', {})
    with pytest.raises(DiagnosticError) as caught:
        debug(f, 'renamed')
    assert 'attempts=1/1' in str(caught.value)


def debug_service(f):
    return f.coordinator.worker_services["tcad_artifact:tcad.development_debug"]


def debug(f, name):
    return f.worker.call_tool("worker_experiment_debug", {"implementation": f.alias, "name": name, "mode": "preflight"})


@pytest.mark.parametrize("scope", ["task", "scientific_subject"])
def test_old_scientific_history_only_charges_configured_shared_scope(tmp_path, monkeypatch, scope):
    f = continuation(tmp_path, monkeypatch)
    service = debug_service(f)
    policy = service.adapter.policy.model_copy(update={"budget_scope": scope})
    monkeypatch.setattr(type(service.adapter), "policy", property(lambda _: policy))
    path = next((service.exchange_root / "budgets").glob("*.json"))
    ledger = json.loads(path.read_bytes())
    ledger["reserved_wall_seconds"] = 100000
    path.write_text(json.dumps(ledger))
    before = service.adapter.submits
    if scope == "scientific_subject":
        with pytest.raises(DiagnosticError) as caught:
            debug(f, "fresh-task")
        assert "debug.total_wall_seconds" in str(caught.value)
        assert "scope=scientific_subject" in str(caught.value)
        assert service.adapter.submits == before
    else:
        result = debug(f, "fresh-task")
        assert result["state"] == "succeeded"
        assert result["budget"]["scope"] == "task"
        assert result["budget"]["used_wall_seconds"] == 1
        assert result["budget"]["created_runs"] == 1
        assert service.adapter.submits == before + 1
        assert json.loads(path.read_bytes())["reserved_wall_seconds"] == 100001


def test_task_limit_has_explicit_admin_recovery_without_erasing_usage(tmp_path, monkeypatch):
    f = continuation(tmp_path, monkeypatch)
    service = debug_service(f)
    policy = service.adapter.policy.model_copy(update={"max_runs": 1})
    monkeypatch.setattr(type(service.adapter), "policy", property(lambda _: policy))
    debug(f, "first")
    with monkeypatch.context() as patch:
        patch.setattr(service.adapter, 'status_details', lambda _: pytest.fail('No remote scans when the attempt limit already rejects admission'), raising=False)
        with pytest.raises(DiagnosticError) as caught:
            debug(f, "renamed")
    assert "debug.max_runs" in str(caught.value) and "attempts=1/1" in str(caught.value)
    policy = policy.model_copy(update={"max_runs": 2})
    result = debug(f, "renamed")
    assert result["budget"]["created_runs"] == 2
    assert result["budget"]["used_wall_seconds"] == 2


def test_new_admission_settles_actual_time_before_checking_remaining_budget(tmp_path, monkeypatch):
    from dataclasses import replace
    f = continuation(tmp_path, monkeypatch)
    service = debug_service(f)
    policy = service.adapter.policy.model_copy(update={"total_wall_seconds": 2})
    monkeypatch.setattr(type(service.adapter), "policy", property(lambda _: policy))
    monkeypatch.setattr(service.adapter, 'clamp_wall_time', lambda prepared, wall_time_seconds:
        replace(prepared, wall_time_seconds=min(2, wall_time_seconds)))
    first = debug(f, 'first')
    assert first['budget']['reserved_wall_seconds'] == 2
    monkeypatch.setattr(service.adapter, 'status_details', lambda _: {'state':'succeeded',
        'consumed_budget':{'wall_time_seconds':1}}, raising=False)
    second = debug(f, 'second')
    assert second['budget']['actual_wall_seconds'] == 1
    assert second['budget']['reserved_wall_seconds'] == 1
    assert second['budget']['used_wall_seconds'] == 2


def test_invisible_submission_cannot_be_retired_without_permanent_absence(tmp_path, monkeypatch):
    f = continuation(tmp_path, monkeypatch)
    service = debug_service(f)
    submit = service.adapter.submit
    def unavailable(*args, **kwargs):
        raise TimeoutError("fixture acknowledgement unavailable")
    monkeypatch.setattr(service.adapter, "submit", unavailable)
    monkeypatch.setattr(service.adapter, "lookup_submission", unavailable)
    with pytest.raises(DiagnosticError):
        debug(f, "uncertain")
    with pytest.raises(Exception):
        f.worker.call_tool("worker_experiment_debug", {"action": "cancel", "name": "uncertain"})
    path = next((service.exchange_root / "budgets").glob("*.json"))
    before = json.loads(path.read_bytes())["reserved_wall_seconds"]
    monkeypatch.setattr(service.adapter, "lookup_submission", lambda _: None)
    with pytest.raises(DiagnosticError) as caught:
        f.worker.call_tool("worker_experiment_debug", {"action": "cancel", "name": "uncertain"})
    assert caught.value.details[0]["code"] == "tcad_debug_retirement_protocol_unavailable"
    after = json.loads(path.read_bytes())
    assert after["reserved_wall_seconds"] == before
    assert "actual_wall_seconds" not in after["reservations"]["uncertain"]["record"]
    status = f.worker.call_tool("worker_experiment_debug", {"action": "status", "name": "uncertain"})
    assert status["allowance_reserved"] is True and status["submission"] == "unknown"
    monkeypatch.setattr(service.adapter, "submit", submit)
    with pytest.raises(DiagnosticError) as caught:
        debug(f, "additional")
    assert caught.value.details[0]["code"] == "tcad_debug_submission_unknown"
    assert "additional" not in json.loads(path.read_bytes())["reservations"]


@pytest.mark.process_e2e
def test_late_review_and_new_task_budget_through_real_proxy(tmp_path, monkeypatch):
    import multiprocessing
    import os
    import subprocess
    import sys
    import time
    from scidiscovery.plugin_runtime.transport import UnixSocketDaemon
    from scidiscovery.artifact_agent.interfaces.mcp import MCPRouter
    f = continuation(tmp_path, monkeypatch, require_execution_review=True)
    subject = f.worker.call_tool('worker_experiment_execute', {'action':'review', 'implementation':f.alias})['review']['subject_artifact_name']
    review(f, 'late-review', subject, 'revise')
    service = debug_service(f)
    policy = service.adapter.policy.model_copy(update={'max_runs':1})
    monkeypatch.setattr(type(service.adapter), 'policy', property(lambda _: policy))
    path = next((service.exchange_root / 'budgets').glob('*.json'))
    old = json.loads(path.read_bytes())
    old['reserved_wall_seconds'] = 100000
    path.write_text(json.dumps(old))
    socket = tmp_path / 'worker.sock'
    process = multiprocessing.get_context('fork').Process(target=lambda:
        UnixSocketDaemon(socket, MCPRouter(f.worker, name='review-budget-test')).serve_forever())
    process.start()
    try:
        deadline = time.monotonic() + 5
        while not socket.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert socket.exists()
        def call(name, arguments):
            request = {'jsonrpc':'2.0', 'id':1, 'method':'tools/call', 'params':{
                'name':name, 'arguments':arguments, '_meta':{'x-codex-turn-metadata':{
                    'session_id':'continuation-fixture', 'thread_id':'next-owner', 'thread_source':'subagent'}}}}
            response = subprocess.run([sys.executable, '-m', 'scidiscovery.artifact_agent.interfaces.mcp_proxy', '--socket', str(socket)],
                input=json.dumps(request)+'\n', text=True, capture_output=True, timeout=10,
                env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
            assert response.returncode == 0, response.stderr
            return json.loads(response.stdout)
        reply = call('worker_experiment_execute', {'action':'review', 'implementation':f.alias})
        assert 'error' not in reply, reply
        result = reply['result']['structuredContent']['review_result']
        assert result['verdict'] == 'revise'
        body = call('worker_reference_read', result['read_request'])
        assert json.loads(body['result']['structuredContent']['fragment'])['verdict'] == 'revise'
        command = {'implementation':f.alias, 'name':'new-task', 'mode':'preflight'}
        result = call('worker_experiment_debug', command)
        assert result['result']['structuredContent']['budget']['created_runs'] == 1
        repeated = call('worker_experiment_debug', command)
        assert repeated['result']['structuredContent']['reference'] == result['result']['structuredContent']['reference']
        error = call('worker_experiment_debug', {**command, 'name':'renamed'})['error']
        assert 'tcad_debug_attempts_exhausted' in json.dumps(error)
        assert 'debug.max_runs' in json.dumps(error)
        assert len(json.loads(path.read_bytes())['reservations']) == len(old['reservations']) + 1
    finally:
        process.terminate()
        process.join(3)
        if process.is_alive():
            process.kill()
            process.join(3)
