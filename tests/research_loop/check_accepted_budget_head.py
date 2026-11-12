"""Bounded accepted budget reads and event/index/head crash recovery."""
from pathlib import Path
import threading

import pytest

from scidiscovery.research_loop import accepted_budget as accepted, atomic_files, budget
from scidiscovery.research_loop import contracts as c, controller, native


def prepare(tmp_path):
    public = tmp_path/'public'
    public.mkdir()
    objective = public/'objective.md'
    objective.write_text('Retain accepted counters and fresh current budget fences.')
    root = tmp_path/'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'], max_tokens=1000,
        max_seconds=60, max_steps=64, model='fixture', reasoning_effort='high', search_policy=None)
    state = accepted.read(root)
    assert state['search_ref']['path'] == 'host-history/breadth-initial.json'
    assert c.read(root/state['search_ref']['path']) == state['search']
    assert 'breadth_snapshot' not in state.prefix
    c.write(root/'host-started.json', budget.clock_stamp())
    budget.transition(root, 'paused', 'fixture_start')
    return root


def test_cold_head_reads_no_accepted_ledger_prefix_and_remains_small(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    sizes = []
    for step in range(1,33):
        budget.transition(root, 'waiting' if step%2 else 'paused', 'fixture_tick')
        if step in (2,8,16,32):
            sizes.append((root/accepted.HEAD).stat().st_size)
    final_seq = accepted.read(root)['seq']
    original = c.read
    reads = []
    def read(path):
        path = Path(path)
        if path.parent == root/'control-ledger':
            raise AssertionError('Ordinary cold read reopened an already accepted event')
        reads.append(path)
        return original(path)
    monkeypatch.setattr(c, 'read', read)
    monkeypatch.setattr(budget, '_read_events', lambda *a, **k: pytest.fail('Implicit full-ledger fold'))
    for _ in range(2):
        assert budget.snapshot(root)['ledger_events'] == final_seq
        assert budget.status_snapshot(root)['clock_phase'] == 'paused'
        assert budget.ceilings(root)['max_tokens'] == 1000
        assert budget.final_a_barrier(root) is None
        assert budget.normal_resume(root) is None
        assert budget.used_steps(root) == 0
    assert max(sizes)-min(sizes) < 256, sizes
    assert reads.count(root/accepted.HEAD) >= 2, 'A second call must observe persisted current state'


def test_crash_after_event_and_indexes_before_head_replays_exact_delta_once(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    before = accepted.read(root)
    replace = atomic_files.replace_bytes
    fail = [True]
    def interrupted(path, payload, *args, **kwargs):
        if Path(path) == root/accepted.HEAD and fail[0]:
            fail[0] = False
            raise OSError('fixture crash before accepted head publication')
        return replace(path, payload, *args, **kwargs)
    monkeypatch.setattr(atomic_files, 'replace_bytes', interrupted)
    ceilings = dict(before['ceilings'], max_tokens=1200)
    operation = 'fixture-budget-amendment'
    with pytest.raises(OSError, match='fixture crash'):
        with budget._ledger_writer(root):
            events, parent = budget._events(root)
            budget._append(root, c.settings(root), events, parent, kind='budget_amend',
                before={key:before['ceilings'][key] for key in ('max_tokens','max_seconds')},
                after={key:ceilings[key] for key in ('max_tokens','max_seconds')},
                authorization_note='Explicit crash-recovery fixture amendment.', operation_id=operation)
    assert c.read(root/accepted.HEAD)['seq'] == before['seq']
    recovered = accepted.read(root)
    assert recovered['seq'] == before['seq']+1
    assert recovered['ceilings'] == ceilings
    with budget._ledger_writer(root):
        accepted.recover(root, accepted.read(root))
    assert accepted.read(root)['seq'] == before['seq']+1
    assert accepted.read(root).row('operation/'+operation)['ref'] == recovered['head_ref']
    assert budget.snapshot(root)['max_tokens'] == 1200
    assert not (root/'control-ledger'/f'{before["seq"]+2:06d}.json').exists()


def test_adjacent_amendment_and_current_meter_are_fresh(tmp_path):
    root = prepare(tmp_path)
    assert budget.snapshot(root)['max_tokens'] == 1000
    budget.amend(root,max_tokens=1200,authorization_note='Explicit fixture revision.')
    assert budget.snapshot(root)['max_tokens'] == 1200
    job = c.build_job(root,1,'E',[])
    state = accepted.read(root)
    assert job['history']['breadth_snapshot'] == state['search_ref']
    assert state.row('job/001')['input_search_ref'] == state['search_ref']
    record = c.host_record(c.workspace(root,1))
    intent = dict(task_ref=c.record_ref(root/'jobs/001.json'))
    accepted.accept_native(root,1,record,intent)
    assert native.usage(root)['unknown_steps'] == [1]
    assert native.usage(root)['blocking_unknown_count'] == 0
    assert budget.execution_blocked(root)
    c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    assert native.usage(root)['observed_tokens'] == 10
    c.write(record/'usage.json',dict(input_tokens=17,cached_input_tokens=2,output_tokens=3))
    assert native.usage(root)['observed_tokens'] == 20
    accepted.accept_terminal(root,1,record,meter=dict(input_tokens=17,cached_input_tokens=2,output_tokens=3),unknown=False)
    assert native.usage(root)['observed_tokens'] == 20
    accepted.accept_terminal(root,1,record,meter=dict(input_tokens=17,cached_input_tokens=2,output_tokens=3),unknown=False)
    assert native.usage(root)['observed_tokens'] == 20


def test_closed_unknown_meter_is_retained_and_only_explicit_blocking_is_excluded(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    c.build_job(root,1,'E',[])
    record = c.host_record(c.workspace(root,1))
    intent = dict(task_ref=c.record_ref(root/'jobs/001.json'))
    accepted.accept_native(root,1,record,intent)
    meter = dict(input_tokens=17,cached_input_tokens=2,output_tokens=3)
    accepted.accept_terminal(root,1,record,meter=meter,unknown=True,
        metering=dict(coverage='unknown',reason='terminal_usage_unreported'))
    retained = native.usage(root)
    assert retained['unknown_steps'] == [1] and retained['unknown_step_count'] == 1
    assert retained['observed_tokens'] == retained['accounted_tokens'] == 20
    assert retained['blocking_unknown_steps'] == [] and retained['blocking_unknown_count'] == 0
    with monkeypatch.context() as no_history:
        no_history.setattr(accepted,'read',lambda *_:pytest.fail('Blocking projection must not reopen history'))
        assert not accepted.unknown_except(root,retained,allowed=[1])
        # An allowed historical metering gap cannot cancel a different step in
        # an explicit blocking projection supplied by another consumer.
        observed = dict(retained,blocking_unknown_steps=[2],blocking_unknown_count=1)
        assert accepted.unknown_except(root,observed,allowed=[1])
        assert not accepted.unknown_except(root,observed,allowed=[2])


def test_recovery_snapshot_advances_current_pointer_and_preserves_admitted_input(tmp_path):
    root = prepare(tmp_path)
    job = c.build_job(root,1,'E',[])
    before = accepted.read(root)
    recovery = root/'execution-authority/calls/fixture/recovery-operations/000001.json'
    recovery.parent.mkdir(parents=True)
    c.write(recovery,dict(step=1,call_id='fixture',status='unknown'))
    snapshot = recovery.parent/'snapshots/000001.json'
    snapshot.parent.mkdir()
    c.write(snapshot,before['search'])
    snapshot.chmod(0o400)
    reference = dict(origin_identity=before.prefix['branch'],
        path=snapshot.relative_to(root).as_posix(),size_bytes=snapshot.stat().st_size)
    delta = dict(summary=before['search'],rows={})
    with pytest.raises(ValueError,match='exact saved snapshot'):
        accepted.publish_recovery(root,step=1,call_id='fixture',recovery_ref=c.record_ref(recovery),
            search_delta=delta,search_ref=None)
    assert accepted.read(root)['seq'] == before['seq']
    after = accepted.publish_recovery(root,step=1,call_id='fixture',recovery_ref=c.record_ref(recovery),
        search_delta=delta,search_ref=reference)
    assert after['search_ref'] == reference
    assert c.read(root/reference['path']) == after['search']
    assert after.prefix == before.prefix
    assert after.current_job == before.current_job
    assert after.row('job/001')['input_search_ref'] == job['history']['breadth_snapshot']
    assert after['usage'] == before['usage']


def test_two_ledger_writers_advance_fresh_head_without_lost_updates(tmp_path):
    root = prepare(tmp_path)
    before = accepted.read(root)['seq']
    ready = threading.Barrier(2)
    errors = []
    def writer(phase):
        try:
            ready.wait(timeout=5)
            with budget._ledger_writer(root):
                accepted.append(root, accepted.read(root), kind='clock',fields=dict(phase=phase))
        except BaseException as error:
            errors.append(error)
    threads = [threading.Thread(target=writer,args=(phase,)) for phase in ('waiting','paused')]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors, errors
    assert accepted.read(root)['seq'] == before+2
