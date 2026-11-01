"""Public progress consumes accepted identities without opening task bodies."""
import copy
import json
import os
from pathlib import Path

import pytest

from scidiscovery.research_loop import accepted_budget, contracts as c, execution, progress, runtime
from test_direct_input_bindings import direct_instance, stopped_seals
from test_history_handoff import finish


def large_job(root, step=1, role='H', parents=()):
    # Use a valid declared instruction so the regression remains large even if
    # material serialization becomes more compact. The production builder owns
    # the complete job and its accepted identity.
    job = c.build_job(root, step, role, list(parents), instruction=(
        'Retain the declared source observations and their stated limitations.\n' * 8192))
    assert (root / 'jobs' / f'{step:03d}.json').stat().st_size > 390_590
    return job


def log_message(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(dict(type='item.completed',
        item=dict(type='agent_message', text=text))) + '\n').encode())


def guard_reader(monkeypatch):
    """Reject body reads, history traversal and all reader-side file writes."""
    original_open, original_os_open = Path.open, os.open
    bodies = {'science-task.json', 'science-output.json', 'result.json',
        'manifest.json', 'material-manifest.json', 'materials.json', 'input-provenance.json'}

    def opening(path, mode='r', *args, **options):
        assert not any(flag in mode for flag in 'wax+'), 'Progress attempted a write'
        assert 'jobs' not in path.parts and path.name not in bodies, (
            f'Progress opened a whole job, scientific body or material manifest: {path}')
        return original_open(path, mode, *args, **options)

    def opening_descriptor(path, flags, *args, **options):
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND), (
            'Progress attempted a descriptor write')
        return original_os_open(path, flags, *args, **options)

    def forbidden(*args, **options):
        pytest.fail('Progress traversed prior jobs, attempts or history')

    monkeypatch.setattr(Path, 'open', opening)
    monkeypatch.setattr(progress.os, 'open', opening_descriptor)
    monkeypatch.setattr(Path, 'iterdir', forbidden)
    monkeypatch.setattr(execution, 'records', forbidden)
    monkeypatch.setattr(execution, 'accepted_intents', forbidden)
    monkeypatch.setattr(accepted_budget.AcceptedState, 'event', forbidden)


def test_large_current_job_uses_one_accepted_head_and_preserves_public_cursor(tmp_path, monkeypatch):
    root, _ = direct_instance(tmp_path)
    large_job(root)
    record = execution.origin(root, 1)
    log_message(record / 'native.jsonl', f'current public draft {root} api_key="private-credential"')
    c.write(record / 'native-intent.json', dict(task_ref=dict(path=str(root / 'jobs/001.json'))))
    c.write(record / 'runner-process.json', runtime.process_identity(
        os.getpid(), dict(path=str(root / 'jobs/001.json'))))
    original_settings, original_load = c.settings, accepted_budget.load
    settings, bases = [], []

    def load_settings(path, job=None):
        value = original_settings(path, job)
        settings.append(value)
        return value

    def load_head(path, *, base):
        bases.append(base)
        return original_load(path, base=base)

    before = (root / accepted_budget.HEAD).read_bytes()
    with monkeypatch.context() as guarded:
        guard_reader(guarded)
        guarded.setattr(c, 'settings', load_settings)
        guarded.setattr(accepted_budget, 'load', load_head)
        guarded.setattr(accepted_budget.AcceptedState, 'row', lambda *args: pytest.fail(
            'The current exact job is already present in the accepted head'))
        first = progress.read(root, 1, limit_bytes=1024)
        second = progress.read(root, 1, cursor=first['cursor'], limit_bytes=1024)
        tail = progress.tail(root, 1, limit_bytes=1024)
    assert len(settings) == len(bases) == 3
    assert all(base is setting for base, setting in zip(bases, settings))
    assert first['role'] == 'H' and first['state'] == 'running'
    assert first['events'][0]['text'] == 'current public draft [private] api_key=[credential]'
    assert first['bytes_read'] <= 1024 and second['events'] == []
    assert tail['events'] == first['events']
    assert (root / accepted_budget.HEAD).read_bytes() == before


def test_large_sealed_historical_job_uses_its_exact_accepted_point_record(tmp_path, monkeypatch):
    root, _ = direct_instance(tmp_path)
    stopped_seals(monkeypatch)
    large_job(root)
    _, _, record = finish(root, 1, 'H', [], action='experiment')
    c.build_job(root, 2, 'W', [1])
    log_message(record / 'native.jsonl', 'sealed historical public draft')
    original_row = accepted_budget.AcceptedState.row
    queried = []

    def exact_row(state, key):
        queried.append(key)
        assert key == 'job/001'
        return original_row(state, key)

    with monkeypatch.context() as guarded:
        guard_reader(guarded)
        guarded.setattr(accepted_budget.AcceptedState, 'row', exact_row)
        page = progress.read(root, 1)
    assert queried == ['job/001']
    assert page['step'] == 1 and page['role'] == 'H'
    assert page['state'] == 'seal_present_unverified'
    assert page['sealed_conclusion'] == dict(step=1, available=True, qualification='not_asserted')
    assert page['events'][0]['text'] == 'sealed historical public draft'


def test_large_job_initial_and_continuation_keep_exact_logs_usage_and_runner_binding(tmp_path, monkeypatch):
    root, _ = direct_instance(tmp_path)
    large_job(root)
    original = execution.origin(root, 1)
    task_ref = c.record_ref(root / 'jobs/001.json')
    initial_intent = dict(task_ref=task_ref)
    accepted_budget.accept_native(root, 1, original, initial_intent)
    initial_meter = dict(input_tokens=10, cached_input_tokens=2, output_tokens=3)
    c.write(original / 'usage.json', initial_meter)
    c.write(original / 'native-outcome.json', dict(returncode=0))
    accepted_budget.accept_terminal(root, 1, original, meter=initial_meter, unknown=False)
    continuation = original / 'continuations/001'
    continuation_intent = dict(task_ref=task_ref, step=1, attempt=1,
        continuation_kind='repair', root_intent_ref=c.record_ref(original / 'native-intent.json'),
        previous=execution.proof(original))
    accepted_budget.accept_native(root, 1, continuation, continuation_intent)
    continuation_meter = dict(input_tokens=100, cached_input_tokens=80, output_tokens=5)
    c.write(continuation / 'usage.json', continuation_meter)
    c.write(continuation / 'runner-process.json', runtime.process_identity(
        os.getpid(), c.record_ref(continuation / 'native-intent.json')))
    log_message(original / 'native.jsonl', 'initial attempt only')
    log_message(continuation / 'native.jsonl', 'continuation attempt only')
    with monkeypatch.context() as guarded:
        guard_reader(guarded)
        first = progress.read(root, 1)
        continued = progress.read(root, 1, attempt=1)
        with pytest.raises(ValueError, match='cursor'):
            progress.read(root, 1, attempt=1, cursor=first['cursor'])
    assert first['events'][0]['text'] == 'initial attempt only'
    assert first['usage']['input_tokens'] == 10
    assert first['state'] == 'native_outcome_present'
    assert continued['events'][0]['text'] == 'continuation attempt only'
    assert continued['usage']['input_tokens'] == 100 and continued['state'] == 'running'
    assert first['role'] == continued['role'] == 'H'
    c.write(continuation / 'runner-process.json', runtime.process_identity(os.getpid(), task_ref))
    with monkeypatch.context() as guarded:
        guard_reader(guarded)
        assert progress.read(root, 1, attempt=1)['state'] == 'unknown'


def test_large_selected_fork_job_uses_its_child_accepted_identity(tmp_path, monkeypatch):
    from scidiscovery.research_loop import controller, fork_runtime, fork_service

    root, _ = direct_instance(tmp_path)
    process_matches = runtime.process_matches
    stopped_seals(monkeypatch)
    large_job(root)
    finish(root, 1, 'H', [], action='experiment')
    finish(root, 2, 'W', [1])
    for step in (1, 2):
        (execution.origin(root, step) / 'runner.lock').touch(exist_ok=True)
    with controller._writer(root):
        pass
    child = root.parent / 'fork-child'
    limits = dict(max_tokens=1000, max_seconds=30, max_steps=2)
    preview = fork_service.preview(root, 1, child, budget=limits)
    fork_service.create(root, 1, child, budget=limits,
        expected_preview_id=preview['preview_id'], operation_id='r8-small-progress-record')
    selected = fork_runtime.build_selected_job(child, 1)
    assert selected['role'] == 'H'
    assert (child / 'jobs/001.json').stat().st_size > 390_590
    record = execution.origin(child, 1)
    log_message(record / 'native.jsonl', 'child selected task public draft')
    child_ref = c.record_ref(child / 'jobs/001.json')
    c.write(record / 'native-intent.json', dict(task_ref=child_ref))
    c.write(record / 'runner-process.json', runtime.process_identity(os.getpid(), child_ref))
    with monkeypatch.context() as guarded:
        guard_reader(guarded)
        guarded.setattr(runtime, 'process_matches', process_matches)
        page = progress.read(child, 1)
    assert page['role'] == 'H' and page['state'] == 'running'
    assert page['events'][0]['text'] == 'child selected task public draft'


@pytest.mark.parametrize('surface', ['current', 'historical'])
@pytest.mark.parametrize('change', [
    dict(step=True), dict(step=2), dict(role=None), dict(role=['H']),
    dict(job_ref=None), dict(job_ref=dict(path='/foreign/jobs/001.json')),
    dict(job_ref=dict(path='jobs/001.json')),
])
def test_progress_rejects_malformed_accepted_job_identity_without_body_fallback(tmp_path, monkeypatch, surface, change):
    root, _ = direct_instance(tmp_path)
    c.build_job(root, 1, 'H', [])
    head = accepted_budget.read(root)
    malformed = dict(copy.deepcopy(head.current_job), **change)
    head['current_job'] = malformed if surface == 'current' and malformed.get('step') == 1 else None
    with monkeypatch.context() as guarded:
        guard_reader(guarded)
        guarded.setattr(accepted_budget, 'load', lambda path, *, base: head)
        guarded.setattr(accepted_budget.AcceptedState, 'row', lambda *args: malformed)
        with pytest.raises(ValueError, match='accepted identity'):
            progress.read(root, 1)


def test_progress_requires_an_authoritative_job_record_even_when_a_job_body_exists(tmp_path, monkeypatch):
    root, _ = direct_instance(tmp_path)
    c.build_job(root, 1, 'H', [])
    head = accepted_budget.read(root)
    head['current_job'] = None
    with monkeypatch.context() as guarded:
        guard_reader(guarded)
        guarded.setattr(accepted_budget, 'load', lambda path, *, base: head)
        guarded.setattr(accepted_budget.AcceptedState, 'row', lambda *args: None)
        with pytest.raises(ValueError, match='no accepted job record'):
            progress.read(root, 1)
