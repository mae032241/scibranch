"""Collect a retained completed native turn without another model invocation."""
import json
import sys

import pytest

from scidiscovery.research_loop import accepted_budget, backends, contracts as c, continuation, controller, execution, native, native_recovery
from scidiscovery.research_loop.output_repair import OutputRepairRequired


THREAD = '11111111-2222-4333-8444-000000000001'
METER = dict(input_tokens=103, cached_input_tokens=80, output_tokens=17)
PAYLOAD = dict(summary='An original retained observation.',
    scientific_result='Transport completion grants no scientific qualification.',
    limitations=['No actual model or scientific execution is used.'],
    implementation_selection=None, next_action='analyze',
    route_reason='Independent interpretation of the retained observation.', search_plan=None)


def retained(record, *, fault=None, code=None, payload=None):
    record.mkdir(parents=True, exist_ok=True)
    text = json.dumps(PAYLOAD if payload is None else payload, ensure_ascii=False, indent=2) + '\n'
    notice = dict(type='error', message='Reconnecting... 2/5')
    if code:notice['error'] = dict(code=code)
    events = [dict(type='thread.started', thread_id=THREAD), dict(type='turn.started'), notice,
        dict(type='item.completed', item=dict(type='agent_message', text=text)),
        dict(type='turn.completed', usage=METER)]
    if fault in {'turn.failed', 'turn.incomplete', 'turn.interrupted'}:
        events.insert(3, dict(type=fault))
    if fault == 'late-error':events.append(notice)
    if fault == 'late-activity':events.append(dict(type='item.started', item=dict(type='command_execution')))
    if fault == 'duplicate':events.append(events[-1])
    if fault == 'missing-terminal':events.pop()
    backend = backends.selected(dict(native_launcher_argv=['codex']))
    failures = [backend.event(event)['failure'] for event in events if backend.event(event).get('failure') is not None]
    outcome = dict(returncode=7 if fault == 'nonzero' else 0,
        native_started=True, cleanup='cleanup_unknown' if fault == 'cleanup' else 'observed_group_cleaned',
        stop_reason='native_failure_event', native_backend='codex', terminal_observed=True,
        native_failure=native_recovery.combine(failures), native_activity_observed=True,
        thread_id=THREAD, usage=dict(**METER, observed_tokens=120, unknown_steps=[1]),
        metering=dict(coverage='unknown', reason='native_failure_event', tokens=METER,
            live_reason='native_session_unavailable' if fault == 'live-meter' else None))
    c.write(record / 'native-outcome.json', outcome)
    c.write(record / 'usage.json', dict(METER, output_tokens=18) if fault == 'meter' else METER)
    (record / 'native.jsonl').write_text(''.join(json.dumps(event) + '\n' for event in events))
    (record / 'result.pending.json').write_text(text + ('changed' if fault == 'output' else ''))
    return outcome


@pytest.mark.parametrize('code', [None, 'invalid_api_key', 'connection_error'])
def test_original_endpoint_resolves_intermediate_error_without_rewriting(tmp_path, code):
    stored = retained(tmp_path, code=code)
    paths = [tmp_path / name for name in ('native-outcome.json', 'usage.json', 'native.jsonl', 'result.pending.json')]
    original = {path: path.read_bytes() for path in paths}
    outcome = c.native_outcome(tmp_path)
    assert outcome['stop_reason'] is None and outcome['native_failure'] is None
    assert outcome['metering']['coverage'] == 'terminal'
    assert outcome['usage'] == stored['usage']
    assert outcome['raw_ref'] == dict(path=str(tmp_path / 'science-output.json'), size_bytes=len(original[paths[-1]]))
    assert all(path.read_bytes() == value for path, value in original.items())
    assert not (tmp_path / 'science-output.json').exists()


@pytest.mark.parametrize('fault', [None, 'turn.failed', 'late-error', 'late-activity',
    'duplicate', 'missing-terminal', 'nonzero', 'cleanup', 'live-meter', 'meter', 'output'])
def test_retained_unknown_routing_diagnostic_yields_only_to_verified_original_completion(tmp_path, fault):
    stored = retained(tmp_path, fault=fault)
    assert stored['native_failure']['category'] == 'unknown'
    stream = tmp_path / 'native.jsonl'
    events = [json.loads(line) for line in stream.read_text().splitlines()]
    for event in events:
        if event.get('type') == 'error':event['message'] = 'Reconnecting... 2/5 (workspace routing discovery timed out)'
    stream.write_text(''.join(json.dumps(event) + '\n' for event in events))
    originals = {tmp_path / name:(tmp_path / name).read_bytes() for name in
        ('native-outcome.json', 'usage.json', 'native.jsonl', 'result.pending.json')}
    current = c.native_outcome(tmp_path)
    if fault is None:
        assert current['stop_reason'] is None and current['native_failure'] is None
        assert current['metering']['coverage'] == 'terminal' and current['usage'] == stored['usage']
    else:
        assert current == stored
    assert all(path.read_bytes() == raw for path, raw in originals.items())


@pytest.mark.parametrize('fault', ['turn.failed', 'turn.incomplete', 'turn.interrupted',
    'late-error', 'late-activity', 'duplicate', 'missing-terminal', 'nonzero',
    'cleanup', 'live-meter', 'meter', 'output'])
def test_unverified_original_remains_stopped(tmp_path, fault):
    stored = retained(tmp_path, fault=fault)
    assert c.native_outcome(tmp_path) == stored


def test_resume_collects_original_and_seals_without_native_or_home_access(tmp_path, monkeypatch):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Preserve the exact original observation and its limitations.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, [sys.executable], max_tokens=1000,
        max_seconds=60, max_steps=3, model='fixture', reasoning_effort='high', search_policy=None)
    c.build_job(root, 1, 'H', [])
    record = execution.origin(root, 1)
    retained(record)
    accepted_budget.accept_native(root, 1, record,
        dict(step=1, task_ref=c.record_ref(root / 'jobs/001.json')))
    identity = c.record_ref(root / 'jobs/001.json')
    for name, pid in [('native-process.json', 999999998), ('runner-process.json', 999999999)]:
        c.write(record / name, dict(task_ref=identity, pid=pid, pgid=pid, sid=pid,
            ppid=1, start_ticks=1, boot_id='retained-original-boot'))
    accepted_budget.accept_terminal(root, 1, record, meter=METER,
        unknown=1 in c.native_outcome(record).get('usage', {}).get('unknown_steps', []),
        metering=c.native_outcome(record)['metering'])
    original = {record / name: (record / name).read_bytes() for name in
        ('native-intent.json', 'native-outcome.json', 'usage.json', 'native.jsonl', 'result.pending.json')}
    def unavailable(*args, **kwargs):
        raise AssertionError('Collecting an original result must not access a launcher or native home')
    monkeypatch.setattr(continuation, 'validate_launcher', unavailable)
    monkeypatch.setattr(native, 'execute_native', unavailable)
    monkeypatch.setattr(native.runtime, 'start', unavailable)
    result = continuation.start(root, 1)
    assert result['scientific_result'] == PAYLOAD['scientific_result']
    assert c.validate(c.workspace(root, 1)) == result
    assert (record / 'result-identity.json').is_file()
    assert (record / 'science-output.json').samefile(record / 'result.pending.json')
    assert all(path.read_bytes() == value for path, value in original.items())
    assert len(execution.records(root, 1)) == 1
    assert not (root / 'jobs/002.json').exists()
    assert native.usage(root)['observed_tokens'] == 120


def test_retained_completed_invalid_output_keeps_existing_repair_binding(tmp_path, monkeypatch):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain the original scientific result and repair its required format only.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, [sys.executable], max_tokens=1000,
        max_seconds=60, max_steps=3, model='fixture', reasoning_effort='high', search_policy=None)
    c.build_job(root, 1, 'H', [])
    record = execution.origin(root, 1)
    payload = {key: value for key, value in PAYLOAD.items() if key != 'summary'}
    retained(record, payload=payload)
    accepted_budget.accept_native(root, 1, record,
        dict(step=1, task_ref=c.record_ref(root / 'jobs/001.json')))
    identity = c.record_ref(root / 'jobs/001.json')
    for name, pid in [('native-process.json', 999999998), ('runner-process.json', 999999999)]:
        c.write(record / name, dict(task_ref=identity, pid=pid, pgid=pid, sid=pid,
            ppid=1, start_ticks=1, boot_id='retained-original-boot'))
    outcome = c.read(record / 'native-outcome.json')
    outcome['execution_processes'] = {name: c.record_ref(record / name) for name in
        ('native-process.json', 'runner-process.json')}
    outcome['budget_slice'] = dict(role='H', dispatches=1, observed_tokens=120,
        limits=dict(max_observed_tokens=1000, max_seconds=60), usage_observed=True)
    c.write(record / 'native-outcome.json', outcome)
    accepted_budget.accept_terminal(root, 1, record, meter=METER,
        unknown=1 in c.native_outcome(record).get('usage', {}).get('unknown_steps', []),
        metering=c.native_outcome(record)['metering'])
    frozen = {record / name: (record / name).read_bytes() for name in
        ('native-outcome.json', 'usage.json', 'native.jsonl', 'result.pending.json')}
    inventory = c.accept_current_inventory(root, 1)
    with pytest.raises(OutputRepairRequired):
        c.seal_result_from_inventory(root, 1, inventory=inventory)
    request = continuation.output_request(root, 1, record, inventory=inventory)
    assert request['code'] == 'invalid_fields'
    assert request['source_attempt'] == 0
    assert request['outcome_ref'] == c.record_ref(record / 'native-outcome.json')
    assert request['raw_ref'] == c.file_ref(record / 'science-output.json')
    continuation._eligible_original(root, 1, [record], successful=True)
    with monkeypatch.context() as active:
        active.setattr(continuation.runtime, 'process_matches', lambda *args: True)
        with pytest.raises(continuation.ResumeUnavailable, match='worker_resume_original_running'):
            continuation._eligible_original(root, 1, [record], successful=True)
    intent = dict(attempt=1, output_repair=request, task_ref=identity,
        root_intent_ref=c.record_ref(record / 'native-intent.json'))
    accepted_budget.accept_native(root, 1, record / 'continuations/001', intent)
    assert continuation._accepted_repair_request(root, 1, intent, record) == request
    assert not (record / 'result-identity.json').exists()
    assert all(path.read_bytes() == value for path, value in frozen.items())


@pytest.mark.parametrize('correction_enabled', [False, True])
def test_recovered_sealed_original_supports_fork_source_consumers(tmp_path, correction_enabled):
    from scidiscovery.research_loop import correction, fork_runtime, fork_service, history, originals
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Keep the exact original observation through an ordinary scientific fork.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, [sys.executable], max_tokens=1000,
        max_seconds=60, max_steps=3, model='fixture', reasoning_effort='high',
        **(dict(correction_policy=correction.DEFAULT_POLICY) if correction_enabled else dict(search_policy=None)))
    c.build_job(root, 1, 'H', [])
    record = execution.origin(root, 1)
    retained(record)
    accepted_budget.accept_native(root, 1, record,
        dict(step=1, task_ref=c.record_ref(root / 'jobs/001.json')))
    identity = c.record_ref(root / 'jobs/001.json')
    for name, pid in [('native-process.json', 999999998), ('runner-process.json', 999999999)]:
        c.write(record / name, dict(task_ref=identity, pid=pid, pgid=pid, sid=pid,
            ppid=1, start_ticks=1, boot_id='retained-original-boot'))
    (record / 'runner.lock').touch()
    accepted_budget.accept_terminal(root, 1, record, meter=METER,
        unknown=1 in c.native_outcome(record).get('usage', {}).get('unknown_steps', []),
        metering=c.native_outcome(record)['metering'])
    original_bytes = {name: (record / name).read_bytes() for name in
        ('native-intent.json', 'native-outcome.json', 'usage.json', 'native.jsonl', 'result.pending.json')}
    c.seal_result(root, 1)
    raw = record / 'science-output.json'
    next_job = c.build_job(root, 2, 'A', [1])
    assert next_job['role'] == 'A' and next_job['source_provenance']
    assert next_job['source_restrictions']['direct_input_count'] > 0
    assert history.direct_sources(root, next_job)[0][1]['scientific_result'] == PAYLOAD['scientific_result']
    assert correction.project(root)['enabled'] is correction_enabled
    with controller._writer(root):
        pass
    child = tmp_path / 'child'
    limits = dict(max_tokens=1000, max_seconds=30, max_steps=1)
    preview = fork_service.preview(root, 2, child, budget=limits)
    fork_service.create(root, 2, child, budget=limits,
        expected_preview_id=preview['preview_id'], operation_id='recovered-original-closure')
    source = originals.node(child, 1)
    stream_entry = next(row for row in source.entries if
        row['origin_identity'] == source.origin_identity and row['path'] == 'native-evidence/001/native.jsonl')
    raw_entry = next(row for row in source.entries if
        row['origin_identity'] == source.origin_identity and row['path'] == 'native-evidence/001/science-output.json')
    assert stream_entry['visibility'] == 'host_proof'
    assert raw_entry['visibility'] == 'historical_science'
    assert originals.source_record(source, 'native.jsonl').read_bytes() == original_bytes['native.jsonl']
    assert not (source.storage_root / raw_entry['storage_path']).exists()
    assert originals.resolve_ref(source, c.read(record / 'result-identity.json')['raw_ref']) == raw
    assert all((record / name).read_bytes() == value for name, value in original_bytes.items())
    for name in ('native-intent.json', 'native-outcome.json', 'usage.json', 'native.jsonl'):
        (record / name).unlink()
    # The correction consumer validates historical controls and the original
    # result; its generated historical task inputs are not a copied body closure.
    if not correction_enabled:
        assert c.validate_original(source)['scientific_result'] == PAYLOAD['scientific_result']
    assert c.validate_original(source, controls_only=True)['scientific_result'] == PAYLOAD['scientific_result']
    child_job = fork_runtime.build_selected_job(child, 2)
    assert history.direct_sources(child, child_job)[0][1]['scientific_result'] == PAYLOAD['scientific_result']
    assert correction.project(child)['enabled'] is correction_enabled
    assert not (child / 'jobs/001.json').exists()


@pytest.mark.parametrize('node_budgets', [False, True])
def test_status_projects_original_completion_without_changing_receipt(tmp_path, capsys, node_budgets):
    from scidiscovery.research_loop import cli, instance_control
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Report the retained original endpoint without starting work.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, [sys.executable], max_tokens=1000,
        max_seconds=60, max_steps=3, model='fixture', reasoning_effort='high', search_policy=None,
        node_budget_policy=({role: dict(max_observed_tokens=1000, max_seconds=60) for role in 'HWAE'}
            if node_budgets else None))
    c.build_job(root, 1, 'H', [])
    record = execution.origin(root, 1)
    retained(record)
    accepted_budget.accept_native(root, 1, record,
        dict(step=1, task_ref=c.record_ref(root / 'jobs/001.json')))
    accepted_budget.accept_terminal(root, 1, record, meter=METER,
        unknown=1 in c.native_outcome(record).get('usage', {}).get('unknown_steps', []),
        metering=c.native_outcome(record)['metering'])
    original = (record / 'native-outcome.json').read_bytes()
    node = instance_control.status(root, live_feedback=False)['nodes'][0]
    assert node['stop_reason'] is None and node['returncode'] == 0
    if node_budgets:
        assert cli.main(['status', '--root', str(root)]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result['nodes'][0]['stop_reason'] is None
    assert (record / 'native-outcome.json').read_bytes() == original
    assert c.read(record / 'native-outcome.json')['stop_reason'] == 'native_failure_event'


def test_completed_worker_keeps_original_unresolved_execution_wait(tmp_path, monkeypatch):
    from scidiscovery.research_loop import managed_wait
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Preserve the original unresolved execution and its waiting checkpoint.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, [sys.executable], max_tokens=1000,
        max_seconds=60, max_steps=3, model='fixture', reasoning_effort='high', search_policy=None)
    job = c.build_job(root, 1, 'W', [])
    record = execution.origin(root, 1)
    retained(record, payload=dict(summary='The original execution remains unresolved.',
        scientific_result='No terminal scientific result has been consumed.',
        limitations=['Await the same accepted execution.'], stage_intent='review'))
    accepted_budget.accept_native(root, 1, record,
        dict(step=1, task_ref=c.record_ref(root / 'jobs/001.json')))
    identity = c.record_ref(root / 'jobs/001.json')
    for name, pid in [('native-process.json', 999999998), ('runner-process.json', 999999999)]:
        c.write(record / name, dict(task_ref=identity, pid=pid, pgid=pid, sid=pid,
            ppid=1, start_ticks=1, boot_id='retained-original-boot'))
    inventory = c.accept_current_inventory(root, 1)
    waiting = dict(calls=[dict(call_id='original-unresolved-call')])
    c.write(record / managed_wait.NAME, waiting)
    original = {path: path.read_bytes() for path in
        (record / 'native-outcome.json', record / 'usage.json', record / managed_wait.NAME)}
    checked = []
    def validate(instance, original_job, original_record):
        checked.append((instance, original_job, original_record))
        return waiting
    monkeypatch.setattr(managed_wait, 'validate', validate)
    monkeypatch.setattr(managed_wait.e, 'load', lambda *_: pytest.fail('The existing waiting checkpoint must not query or submit another call'))
    with pytest.raises(managed_wait.ExecutionWaiting):
        managed_wait.before_seal(root, job, record, inventory=inventory)
    assert checked == [(root, job, record)]
    assert all(path.read_bytes() == value for path, value in original.items())
