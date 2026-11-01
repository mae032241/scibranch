"""Known audit operations read their exact originals without discovering history."""
from collections import Counter
from pathlib import Path

import pytest

from scidiscovery.research_loop import (
    audit_recovery, candidate_audit, candidate_refs, configuration_updates,
    contracts as c, controller, delivery, execution, runtime,
)


OPERATION = 'a' * 64


@pytest.fixture
def point_scene(tmp_path, monkeypatch):
    root = tmp_path / 'instance'
    settings = dict(delivery_policy=dict(submit_seconds=20))
    c.write(root / 'settings.json', settings)
    monkeypatch.setattr(configuration_updates, 'for_job', lambda root, job: settings)
    monkeypatch.setattr(configuration_updates, 'for_step', lambda root, step: settings)
    monkeypatch.setattr(controller, 'require_writer', lambda root: None)
    monkeypatch.setattr(runtime, 'process_matches', lambda owner, operation: False)
    monkeypatch.setattr(delivery, 'leaf_refs', lambda root, operation: [])
    return root


def recovery_record(root, operation=OPERATION, *, status='engineering_error', cleanup=True,
                    outcome=True, process=True):
    directory = root / 'host-delivery/audit-recovery' / operation
    intent = dict(settings_ref=c.record_ref(root / 'settings.json'), operation_id=operation,
                  attempt=1, reason='audit_process_failed', max_seconds=10, advance_seconds=0,
                  fence_protocol=1, owner={}, initial_result=dict(status='engineering_error',
                  cleanup_confirmed=True), leaf_output=[])
    c.write(directory / 'intent.json', intent)
    if process:
        c.write(directory / 'process.json', dict(phase='audit', identity=dict(task_ref=operation)))
    value = dict(intent_ref=c.record_ref(directory / 'intent.json'), status=status,
                 note='Exact original recovery', cleanup_confirmed=cleanup,
                 process_ref=c.record_ref(directory / 'process.json') if process else None)
    if outcome:
        c.write(directory / 'outcome.json', value)
    return directory, intent, value


def candidate_record(root, monkeypatch):
    step = 3
    record = c.host_record(c.workspace(root, step))
    raw = record / 'science-output.json'
    c.write(root / 'jobs/003.json', dict(role='A', handoff_protocol=9))
    c.write(raw, dict(next_action='audit', candidate_recommendation='run-1'))
    c.write(record / 'result-identity.json', {})
    monkeypatch.setattr(execution, 'raw', lambda root, step: raw)
    monkeypatch.setattr(candidate_refs, 'control_payload', lambda job, value, **kwargs: value)
    decision = candidate_audit._decision(root, step)
    operation = candidate_audit._operation(root, decision, 'run-1')
    directory = candidate_audit._directory(root, step)
    intent = dict(action_id=operation, action_kind='audit', operation_id=operation,
                  decision=decision, candidate_id='run-1', max_seconds=20, advance_seconds=0)
    c.write(directory / 'intent.json', intent)
    recovery, _, _ = recovery_record(root, operation)
    result = dict(status='engineering_error', note='Original stopped preflight',
                  cleanup_confirmed=True, operation_id=operation, candidate_id='run-1', artifact_ref=None)
    c.write(directory / 'adapter-result.json', result)
    value = dict(decision=decision, candidate_id='run-1', accepted=True, result=result,
                 intent_ref=c.record_ref(directory / 'intent.json'), binding_ref=None,
                 result_ref=c.record_ref(directory / 'adapter-result.json'), process_ref=None,
                 recovery_ref=c.record_ref(recovery / 'outcome.json'))
    c.write(directory / 'outcome.json', value)
    return directory, recovery, value


def unrelated_history(root):
    # Complete, valid historical retries must not add work to a point query.
    directories = set()
    for number in range(64):
        operation = f'{number:064x}'
        directory, _, _ = recovery_record(root, operation)
        directories.add(directory)
    return directories


def observed_query(monkeypatch, unrelated, query):
    """Count JSON/stat access and forbid discovery while the production query runs."""
    counts = Counter()
    read, stat, lstat = c.read, Path.stat, Path.lstat

    def check(path):
        assert path not in unrelated and path.parent not in unrelated, path

    def counted_read(path):
        check(path)
        counts['json'] += 1
        return read(path)

    def counted_stat(path, *args, **kwargs):
        check(path)
        counts['stat'] += 1
        return stat(path, *args, **kwargs)

    def counted_lstat(path, *args, **kwargs):
        check(path)
        counts['lstat'] += 1
        return lstat(path, *args, **kwargs)

    def no_discovery(*args, **kwargs):
        pytest.fail('A known audit operation must not enumerate history')

    with monkeypatch.context() as patch:
        patch.setattr(c, 'read', counted_read)
        patch.setattr(Path, 'stat', counted_stat)
        patch.setattr(Path, 'lstat', counted_lstat)
        patch.setattr(Path, 'glob', no_discovery)
        patch.setattr(Path, 'iterdir', no_discovery)
        value = query()
    return value, counts


def test_candidate_receipt_reads_only_bound_recovery(point_scene, monkeypatch):
    root = point_scene
    _, _, expected = candidate_record(root, monkeypatch)
    query = lambda: candidate_audit._outcome(root, 3)
    value, before = observed_query(monkeypatch, set(), query)
    assert value == expected
    unrelated = unrelated_history(root)
    value, after = observed_query(monkeypatch, unrelated, query)
    assert value == expected and after == before


@pytest.mark.parametrize('outcome', [False, True])
def test_recover_preflight_reads_only_exact_operation(point_scene, monkeypatch, outcome):
    from scidiscovery.research_loop import accepted_budget
    root = point_scene
    completed = []
    monkeypatch.setattr(accepted_budget,'finish_audit',lambda actual_root,operation:
        completed.append((actual_root,operation)))
    directory, _, expected = recovery_record(root, outcome=outcome, process=outcome)
    unrelated = unrelated_history(root)
    result, _ = observed_query(monkeypatch, unrelated,
        lambda: audit_recovery.recover_preflight(root, OPERATION))
    if outcome:
        assert result == expected
        assert completed == []
    else:
        assert result == dict(status='engineering_error', note='audit_recovery_preflight_not_dispatched',
                              cleanup_confirmed=True)
        assert c.read(directory / 'outcome.json')['intent_ref'] == c.record_ref(directory / 'intent.json')
        assert completed == [(root,OPERATION)]


@pytest.mark.parametrize('damage', ['operation', 'settings', 'intent_ref', 'process_ref', 'process_identity'])
def test_point_and_full_facts_share_original_validation(point_scene, damage):
    root = point_scene
    directory, intent, value = recovery_record(root)
    if damage == 'operation':
        intent['operation_id'] = 'b' * 64
        c.write(directory / 'intent.json', intent)
    elif damage == 'settings':
        intent['settings_ref'] = dict(path=str(root / 'other-settings.json'))
        c.write(directory / 'intent.json', intent)
    elif damage == 'intent_ref':
        value['intent_ref'] = dict(path=str(directory / 'other-intent.json'))
        c.write(directory / 'outcome.json', value)
    elif damage == 'process_ref':
        value['process_ref'] = dict(path=str(directory / 'other-process.json'))
        c.write(directory / 'outcome.json', value)
    else:
        c.write(directory / 'process.json', dict(phase='audit', identity=dict(task_ref='b' * 64)))
    with pytest.raises(ValueError, match='recovery .*changed'):
        audit_recovery.recover_preflight(root, OPERATION)
    with pytest.raises(ValueError, match='recovery .*changed'):
        list(audit_recovery.facts(root))


@pytest.mark.parametrize('damage', ['ref', 'operation', 'unknown'])
def test_candidate_recovery_requires_exact_clean_original(point_scene, monkeypatch, damage):
    root = point_scene
    directory, recovery, value = candidate_record(root, monkeypatch)
    if damage == 'ref':
        value['recovery_ref'] = dict(path=str(recovery / 'other-outcome.json'))
        c.write(directory / 'outcome.json', value)
    elif damage == 'operation':
        intent = c.read(recovery / 'intent.json')
        intent['operation_id'] = 'b' * 64
        c.write(recovery / 'intent.json', intent)
    else:
        outcome = c.read(recovery / 'outcome.json')
        outcome.update(status='unknown', cleanup_confirmed=False)
        c.write(recovery / 'outcome.json', outcome)
    with pytest.raises(ValueError, match='recovery .*changed'):
        candidate_audit._outcome(root, 3)


def test_unknown_recovery_keeps_original_and_cleanup_gate(point_scene, monkeypatch):
    root = point_scene
    directory, _, expected = recovery_record(root, status='unknown', cleanup=False)
    originals = {name: (directory / name).read_bytes()
                 for name in ('intent.json', 'outcome.json', 'process.json')}
    unrelated = unrelated_history(root)
    value, _ = observed_query(monkeypatch, unrelated,
        lambda: audit_recovery.recover_preflight(root, OPERATION))
    assert value == expected and value['cleanup_confirmed'] is False
    assert all((directory / name).read_bytes() == content for name, content in originals.items())


def test_dispatched_recovery_without_outcome_remains_unknown(point_scene, monkeypatch):
    root = point_scene
    directory, _, _ = recovery_record(root, outcome=False, process=True)
    original = (directory / 'intent.json').read_bytes()
    unrelated = unrelated_history(root)
    value, _ = observed_query(monkeypatch, unrelated,
        lambda: audit_recovery.recover_preflight(root, OPERATION))
    assert value is None and not (directory / 'outcome.json').exists()
    assert (directory / 'intent.json').read_bytes() == original
