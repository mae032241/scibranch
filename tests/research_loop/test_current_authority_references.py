"""Current authority originals use the existing acceptance and one-tail redo.

These are control-transport fixtures. Admission/source selection are isolated;
no provider, native process, scientific qualification or dispatch is exercised.
"""
import copy
import json
from pathlib import Path

import pytest

from scidiscovery.operations.execution import LocalFileDescriptor
from scidiscovery.research_loop import accepted_budget as accepted, budget
from scidiscovery.research_loop import contracts as c, execution, execution_authority as authority
from scidiscovery.research_loop import execution_resume, experiment_client, native, qualification as q
from scidiscovery.research_loop import worker_execution_bridge
from test_execution_authority import declared_request


def _producer(tmp_path, monkeypatch):
    root, secret, arguments = declared_request(tmp_path)
    job = c.read(root/'jobs/002.json')
    record = root/'native-evidence/002'
    accepted.accept_native(root, 2, record, c.read(record/'native-intent.json'))
    alias = {key: arguments['subject']['material']['source' if key == 'path' else key]
        for key in ('path', 'size_bytes')}
    config = dict(scope=dict(step=2, job_ref=c.record_ref(root/'jobs/002.json')),
        native_attempt=dict(number=0), execution_interface=dict(executor='transport-fixture'),
        record_dir=str(experiment_client.record_directory(root, 2)))
    payload = LocalFileDescriptor(name='transport-only', local_path=alias['path'],
        size_bytes=alias['size_bytes'], media_type='application/octet-stream')
    monkeypatch.setattr(authority, '_host_context', lambda _: (root, job, arguments['native_intent']))
    monkeypatch.setattr(authority, '_selected_subject',
        lambda *args: (arguments['subject'], arguments['review_ref'], alias))
    monkeypatch.setattr(authority, '_remaining', lambda *args: (budget.clock_stamp(), 60.))
    monkeypatch.setattr(budget, 'snapshot', lambda *args, **kwargs: {})
    monkeypatch.setattr(budget, 'execution_available', lambda *args, **kwargs: dict(seconds=60.))
    monkeypatch.setattr(native, 'usage', lambda *args, **kwargs: {})
    monkeypatch.setattr(worker_execution_bridge, 'stopped', lambda *args: None)
    capture_job = dict(job, execution_authority=authority.contract(root, job))
    return root, config, payload, arguments, capture_job


def _allocate(config, payload):
    return authority._allocate_call(config, payload, 1)


def _only_call(root):
    head = accepted.read(root)
    calls = accepted.authority_calls(head, 2)
    assert len(calls) == 1
    call = next(iter(calls))
    return head, call, calls[call]


def _canonical(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf-8')


@pytest.mark.parametrize('history_count', [0, 64])
def test_first_capture_reads_only_current_producer_references(tmp_path, monkeypatch, history_count):
    root, config, payload, arguments, job = _producer(tmp_path, monkeypatch)
    call = _allocate(config, payload)
    request = authority.create_request(root, **dict(arguments, call_id=call))
    head, _, refs = _only_call(root)
    assert head.current_job['authority_calls'] == {call: refs}
    assert refs['requests'] == [request]
    historical = set()
    # Completed unrelated originals are deliberately outside the accepted
    # current job. Their bytes must never enter this first-capture decision.
    for number in range(history_count):
        old_call = f'{number+2:04d}-' + f'{number:032x}'
        allocation = root/'execution-authority/calls'/old_call/'allocation.json'
        old_request = root/'execution-authority/requests'/f'{number:064x}'/'request.json'
        q._save(root, allocation, dict(call_id=old_call, completed=True,
            binding=dict(scope=dict(job_ref=dict(path=str(root/'jobs/001.json'))))))
        q._save(root, old_request, dict(call_id=old_call, completed=True,
            job=dict(path='jobs/001.json', size_bytes=1)))
        historical.update((allocation, old_request))
    read, glob, iterdir, stat = c.read, Path.glob, Path.iterdir, Path.stat
    authority_reads = []
    def current_read(path):
        path = Path(path)
        assert path not in historical, 'First capture reopened unrelated authority history'
        if path.parent.parent in (root/'execution-authority/calls', root/'execution-authority/requests'):
            authority_reads.append(path)
        return read(path)
    def bounded_glob(path, *args, **kwargs):
        assert path not in (root/'execution-authority/calls', root/'execution-authority/requests')
        return glob(path, *args, **kwargs)
    def bounded_iterdir(path):
        assert path not in (root/'execution-authority/calls', root/'execution-authority/requests')
        return iterdir(path)
    def bounded_stat(path, *args, **kwargs):
        assert path not in historical
        return stat(path, *args, **kwargs)
    monkeypatch.setattr(c, 'read', current_read)
    monkeypatch.setattr(Path, 'glob', bounded_glob)
    monkeypatch.setattr(Path, 'iterdir', bounded_iterdir)
    monkeypatch.setattr(Path, 'stat', bounded_stat)
    pending = q._read(root, execution_resume._capture_pending(root, job))
    assert len(authority_reads) == 2
    assert set(authority_reads) == {root/refs['allocation']['path'], root/request['path']}
    assert pending['allocations'][0]['allocation'] == refs['allocation']
    assert pending['allocations'][0]['requests'] == [request]
    assert pending['allocations'][0]['disposition'] == 'unprepared'
    assert pending['complete'] is False  # This fixture has no native cleanup originals.


def test_allocation_tail_preserves_original_before_prepare_and_retry(tmp_path, monkeypatch):
    root, config, payload, _, job = _producer(tmp_path, monkeypatch)
    before = accepted.read(root)
    usage, resource = copy.deepcopy(before['usage']), copy.deepcopy(before['resource'])
    def interrupted(*args):
        raise OSError('allocation original/head installation interrupted')
    with monkeypatch.context() as patch:
        patch.setattr(accepted, '_install_indexes', interrupted)
        with pytest.raises(OSError, match='installation interrupted'):
            _allocate(config, payload)
        persisted_head = (root/accepted.HEAD).read_bytes()
        head, call, refs = _only_call(root)
        original = accepted.authority_original(head, refs['allocation'])
        path = root/refs['allocation']['path']
        assert not path.exists() and not (path.parent/'prepare-intent.json').exists()
        pending = q._read(root, execution_resume._capture_pending(root, job))
        assert pending['allocations'][0]['allocation'] == refs['allocation']
        assert pending['allocations'][0]['requests'] == []
        assert pending['allocations'][0]['disposition'] == 'unprepared'
        assert pending['complete'] is False
        assert not path.exists() and (root/accepted.HEAD).read_bytes() == persisted_head
    seen = []
    def same_original(root_, config_, retained=None):
        seen.append(retained)
        assert retained == original
        return budget.clock_stamp(), 1.
    monkeypatch.setattr(authority, '_remaining', same_original)
    assert _allocate(config, payload) == call
    assert _allocate(config, payload) == call
    assert seen == [original, original]
    restored = accepted.read(root)
    assert restored.pending is None and restored['seq'] == head['seq'] == before['seq']+1
    assert restored['usage'] == usage and restored['resource'] == resource
    assert c.read(path) == original and path.read_bytes() == _canonical(original)
    assert q._ref(root, path) == refs['allocation']
    assert path.stat().st_mode & 0o777 == 0o400


@pytest.mark.parametrize('renewal', [False, True])
def test_request_tail_keeps_original_and_successor_before_return(tmp_path, monkeypatch, renewal):
    root, config, payload, arguments, job = _producer(tmp_path, monkeypatch)
    call = _allocate(config, payload)
    arguments = dict(arguments, call_id=call)
    previous = authority.create_request(root, **arguments) if renewal else None
    if renewal:
        arguments.update(expires_at=arguments['expires_at']+10, supersedes=previous)
    before = accepted.read(root)
    def interrupted(*args):
        raise OSError('request original/head installation interrupted')
    with monkeypatch.context() as patch:
        patch.setattr(accepted, '_install_indexes', interrupted)
        with pytest.raises(OSError, match='installation interrupted'):
            authority.create_request(root, **arguments)
        persisted_head = (root/accepted.HEAD).read_bytes()
        head, _, refs = _only_call(root)
        reference = refs['requests'][-1]
        original = accepted.authority_original(head, reference)
        path = root/reference['path']
        assert not path.exists()
        if renewal:
            successor = root/Path(previous['path']).parent/'successor.json'
            assert head.control_file(successor) == dict(previous=previous, successor=reference)
            assert not successor.exists()
        pending = q._read(root, execution_resume._capture_pending(root, job))
        assert pending['allocations'][0]['requests'] == ([previous, reference] if renewal else [reference])
        assert pending['complete'] is False
        assert not path.exists() and (root/accepted.HEAD).read_bytes() == persisted_head
    assert authority.create_request(root, **arguments) == reference
    assert authority.create_request(root, **arguments) == reference
    restored = accepted.read(root)
    assert restored.pending is None and restored['seq'] == head['seq'] == before['seq']+1
    assert restored['usage'] == before['usage'] and restored['resource'] == before['resource']
    assert path.read_bytes() == _canonical(original) and q._ref(root, path) == reference
    if renewal:
        assert authority.request_successor(root, previous) == reference
        assert successor.read_bytes() == _canonical(dict(previous=previous, successor=reference))


def test_missing_job_authority_references_fail_closed(tmp_path, monkeypatch):
    root, _, _, arguments, job = _producer(tmp_path, monkeypatch)
    head = accepted.read(root)
    row = head.row('job/002')
    row.pop('authority_calls')
    with budget._ledger_writer(root):
        accepted.append(root, head, kind='execution_recovery', updates=dict(current_job=row), rows={'job/002': row})
    with pytest.raises(ValueError, match='authority references are required'):
        authority.create_request(root, **arguments)
    pending = q._read(root, execution_resume.capture_pending(root, job))
    assert pending['complete'] is False
    assert pending['allocations'] == [] and 'unavailable_reason' in pending


def test_request_acceptance_retains_operation_context_at_ledger_writer(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    root, _, arguments = declared_request(tmp_path)
    context = SimpleNamespace(remaining_seconds=lambda: 10.)
    original = budget._ledger_writer
    observed = []
    @contextmanager
    def bounded_writer(root_, *, context=None):
        observed.append(context)
        with original(root_, context=context) as base:
            yield base
    monkeypatch.setattr(budget, '_ledger_writer', bounded_writer)
    reference = authority.create_request(root, **arguments, operation_context=context)
    assert observed == [context]
    assert accepted.authority_calls(accepted.read(root), 2)[arguments['call_id']]['requests'] == [reference]
