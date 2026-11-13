"""Real control writers retain previous rows; no scientific validation or launch."""
import copy
from pathlib import Path

import pytest

from scidiscovery.research_loop import accepted_budget as accepted, atomic_files, budget
from scidiscovery.research_loop import contracts as c
from test_accepted_budget_head import prepare


METER = dict(input_tokens=7, cached_input_tokens=2, output_tokens=3)


def _open_native(tmp_path):
    root = prepare(tmp_path)
    c.build_job(root, 1, 'E', [])
    record = c.host_record(c.workspace(root, 1))
    accepted.accept_native(root, 1, record, dict(task_ref=c.record_ref(root/'jobs/001.json')))
    return root, record


def _attempt_key(root, record):
    return 'attempt/'+record.relative_to(root).as_posix().replace('/', ':')


def _terminal(root, record, *, unknown=False):
    return accepted.accept_terminal(root, 1, record, meter=dict(METER), unknown=unknown,
        metering=dict(coverage='terminal', reporting_delay_unmeasured=False,
            inflight_usage_unknown=False))


def _seal_arguments(root, record, state):
    # The writer accepts an already selected original reference. This tiny
    # control fixture makes no claim about the scientific owner/validation chain.
    result = record/'writer-result.json'
    c.write(result, dict(control_fixture='accepted writer publication only'))
    result.chmod(0o400)
    return dict(prefix=copy.deepcopy(state.prefix), role='E', result_ref=c.record_ref(result),
        search_ref=copy.deepcopy(state['search_ref']))


def _observe_reads(monkeypatch):
    read = c.read
    paths = []

    def observed(path):
        paths.append(Path(path))
        return read(path)

    monkeypatch.setattr(c, 'read', observed)
    return paths


def _assert_generation(root, before, after, key, previous):
    current = after.row(key)
    assert c.read(accepted._row_path(root, key)) == dict(seq=after['seq'],
        previous_seq=before['seq'], previous=previous, current=current)
    assert before.row(key) == previous
    return current


def test_terminal_writer_reads_each_previous_row_once(tmp_path, monkeypatch):
    root, record = _open_native(tmp_path)
    keys = (_attempt_key(root, record), 'job/001')
    before = accepted.read(root)
    previous_job = copy.deepcopy(before.current_job)
    paths = _observe_reads(monkeypatch)

    after = _terminal(root, record)

    counts = {key: paths.count(accepted._row_path(root, key)) for key in keys}
    assert counts == {keys[0]: 1, keys[1]: 0}, counts
    assert paths.count(root/accepted.HEAD) == 1
    assert before.current_job == previous_job
    assert c.read(accepted._row_path(root, keys[1]))['previous'] == previous_job
    assert after.current_job['status'] == 'terminal'


def test_seal_writer_reads_job_previous_once(tmp_path, monkeypatch):
    root, record = _open_native(tmp_path)
    terminal = _terminal(root, record)
    previous_job = copy.deepcopy(terminal.current_job)
    arguments = _seal_arguments(root, record, terminal)
    paths = _observe_reads(monkeypatch)

    sealed = accepted.publish_scientific_seal(root, 1, **arguments)

    job_reads = paths.count(accepted._row_path(root, 'job/001'))
    assert job_reads == 0, job_reads
    assert paths.count(root/accepted.HEAD) == 1
    assert terminal.current_job == previous_job
    assert c.read(accepted._row_path(root, 'job/001'))['previous'] == previous_job
    assert sealed.current_job['status'] == 'sealed'


@pytest.mark.parametrize('unknown', [False, True])
def test_terminal_preserves_previous_rows_and_accounts_idempotently(tmp_path, unknown):
    root, record = _open_native(tmp_path)
    before = accepted.read(root)
    keys = (_attempt_key(root, record), 'job/001')
    previous = {key: copy.deepcopy(before.row(key)) for key in keys}

    after = _terminal(root, record, unknown=unknown)

    assert after['seq'] == before['seq']+1
    attempt = _assert_generation(root, before, after, keys[0], previous[keys[0]])
    job = _assert_generation(root, before, after, keys[1], previous[keys[1]])
    assert previous[keys[0]]['status'] == 'open'
    assert previous[keys[1]]['status'] == 'prepared'
    assert attempt['status'] == 'closed' and attempt['meter'] == METER
    assert attempt['unknown'] is unknown
    assert job['status'] == 'terminal' and job['complete'] is (not unknown)
    assert job['unknown_count'] == int(unknown)
    assert job['reported_tokens'] == 10
    assert after['current_native'] is None and after.current_job == job
    expected_usage = dict(before['usage'])
    for name, amount in METER.items():
        expected_usage[name] += amount
    expected_usage['unknown_count'] += int(unknown)
    if unknown:
        expected_usage['first_unknown_step'] = 1
    expected_roles = dict(before['role_usage'])
    expected_roles['E'] += 10
    assert after['usage'] == expected_usage and after['role_usage'] == expected_roles

    frozen_head = (root/accepted.HEAD).read_bytes()
    repeated = _terminal(root, record, unknown=unknown)
    assert dict(repeated) == dict(after)
    with pytest.raises(ValueError, match='Closed native accounting changed'):
        accepted.accept_terminal(root, 1, record,
            meter=dict(METER, output_tokens=METER['output_tokens']+1), unknown=unknown)
    with pytest.raises(ValueError, match='Closed native accounting changed'):
        _terminal(root, record, unknown=not unknown)
    assert (root/accepted.HEAD).read_bytes() == frozen_head
    assert not (root/'control-ledger'/f'{after["seq"]+1:06d}.json').exists()


def test_seal_preserves_terminal_previous_and_rejects_two_generation_old_job(tmp_path):
    root, record = _open_native(tmp_path)
    before_terminal = accepted.read(root)
    open_attempt = before_terminal.row(_attempt_key(root, record))
    terminal = _terminal(root, record)
    previous_job = copy.deepcopy(terminal.row('job/001'))
    arguments = _seal_arguments(root, record, terminal)

    sealed = accepted.publish_scientific_seal(root, 1, **arguments)

    assert sealed['seq'] == terminal['seq']+1
    job = _assert_generation(root, terminal, sealed, 'job/001', previous_job)
    assert previous_job['status'] == 'terminal' and 'result_ref' not in previous_job
    assert job['status'] == 'sealed' and job['result_ref'] == arguments['result_ref']
    assert job['output_search_ref'] == arguments['search_ref']
    assert sealed.current_job == job and sealed.prefix == arguments['prefix']
    assert sealed['usage'] == terminal['usage'] and sealed['role_usage'] == terminal['role_usage']
    # The attempt has advanced once; the job has advanced twice. Explicit
    # ownership must retain the existing per-row double-generation limit.
    assert before_terminal.row(_attempt_key(root, record)) == open_attempt
    with pytest.raises(RuntimeError, match='read a fresh head'):
        before_terminal.row('job/001')

    frozen_head = (root/accepted.HEAD).read_bytes()
    repeated = accepted.publish_scientific_seal(root, 1, **arguments)
    assert dict(repeated) == dict(sealed)
    for changed in (dict(result_ref=dict(path=str(root/'different-result.json'))),
            dict(search_ref=dict(path='different-search.json'))):
        with pytest.raises(ValueError, match='changed its accepted result'):
            accepted.publish_scientific_seal(root, 1, **dict(arguments, **changed))
    assert (root/accepted.HEAD).read_bytes() == frozen_head
    assert not (root/'control-ledger'/f'{sealed["seq"]+1:06d}.json').exists()


@pytest.mark.parametrize(('operation', 'boundary'), [
    ('terminal', 'before_indexes'),
    ('terminal', 'partial_indexes'),
    ('terminal', 'before_head'),
    ('seal', 'before_indexes'),
    ('seal', 'before_head'),
])
def test_writer_single_tail_recovers_exact_previous_and_retries_once(
        tmp_path, monkeypatch, operation, boundary):
    root, record = _open_native(tmp_path)
    if operation == 'seal':
        _terminal(root, record)
    before = accepted.read(root)
    keys = ((_attempt_key(root, record), 'job/001') if operation == 'terminal' else ('job/001',))
    previous = {key: copy.deepcopy(before.row(key)) for key in keys}
    arguments = _seal_arguments(root, record, before) if operation == 'seal' else None

    def publish():
        if operation == 'terminal':
            return _terminal(root, record)
        return accepted.publish_scientific_seal(root, 1, **arguments)

    row_paths = {accepted._row_path(root, key) for key in keys}
    replace = atomic_files.replace_bytes
    installed = []

    def interrupted(path, payload, *args, **kwargs):
        path = Path(path)
        if path == root/accepted.HEAD and boundary == 'before_head':
            raise OSError('fixture writer publication interrupted')
        if path in row_paths:
            if boundary == 'before_indexes' or (boundary == 'partial_indexes' and len(installed) == 1):
                raise OSError('fixture writer publication interrupted')
            result = replace(path, payload, *args, **kwargs)
            installed.append(path)
            return result
        return replace(path, payload, *args, **kwargs)

    before_head = (root/accepted.HEAD).read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(atomic_files, 'replace_bytes', interrupted)
        with pytest.raises(OSError, match='writer publication interrupted'):
            publish()
    assert len(installed) == (0 if boundary == 'before_indexes' else
        1 if boundary == 'partial_indexes' else len(keys))
    assert (root/accepted.HEAD).read_bytes() == before_head
    event_path = root/'control-ledger'/f'{before["seq"]+1:06d}.json'
    event = c.read(event_path)
    frozen_event = event_path.read_bytes()
    assert event['kind'] == ('native_terminal' if operation == 'terminal' else 'scientific_seal')
    assert set(event['index_updates']) == set(keys)
    before_read_rows = {path: path.read_bytes() for path in row_paths}
    pending = accepted.read(root)
    assert pending.pending is not None and pending['seq'] == before['seq']+1
    for key in keys:
        update = event['index_updates'][key]
        assert update['previous_seq'] == before['seq'] and update['previous'] == previous[key]
        assert before.row(key) == previous[key]
        assert pending.row(key) == update['current']
    # Reading the accepted tail does not install either rows or head.
    assert (root/accepted.HEAD).read_bytes() == before_head
    assert {path: path.read_bytes() for path in row_paths} == before_read_rows

    with budget._ledger_writer(root):
        recovered = accepted.recover(root, accepted.read(root))
    assert recovered.pending is None and dict(recovered) == dict(pending)
    for key in keys:
        _assert_generation(root, before, recovered, key, previous[key])
    if operation == 'terminal':
        for name, amount in METER.items():
            assert recovered['usage'][name] == before['usage'][name]+amount
        assert recovered['role_usage']['E'] == before['role_usage']['E']+10
    else:
        assert recovered['usage'] == before['usage']
        assert recovered['role_usage'] == before['role_usage']

    recovered_head = (root/accepted.HEAD).read_bytes()
    assert dict(publish()) == dict(recovered)
    assert (root/accepted.HEAD).read_bytes() == recovered_head
    assert event_path.read_bytes() == frozen_event
    assert not (root/'control-ledger'/f'{recovered["seq"]+1:06d}.json').exists()
