"""Reboot reentry uses accepted originals and retains interrupted billing."""
import json
import sys

import pytest

from scidiscovery.research_loop import accepted_budget as accepted
from scidiscovery.research_loop import budget, continuation, contracts as c
from scidiscovery.research_loop import controller, execution, profiles, runtime


THREAD = '11111111-2222-4333-8444-555555555555'
METER = dict(input_tokens=10, cached_input_tokens=3, output_tokens=4)


def accepted_interrupted_native(tmp_path, monkeypatch, *, after_wake=False, meter=True,
        role='W', fake=None, closed_clock=False):
    public = tmp_path/'public'
    public.mkdir()
    objective = public/'objective.md'
    objective.write_text('Retain the exact interrupted native task and its observed billing.')
    launcher = tmp_path/'launcher.py'
    launcher.write_text(fake or 'raise AssertionError("A source fixture must never launch a model")\n')
    root = tmp_path/'instance'
    controller.prepare(root, objective, public, [sys.executable, str(launcher)],
        max_tokens=1000, max_seconds=120, max_steps=6, model='no-model',
        reasoning_effort='high', node_budget_mode='soft', search_policy=None,
        node_budget_policy={role: dict(max_observed_tokens=100, max_seconds=20) for role in 'HWAE'},
        resume_policy=dict(max_attempts=2, protocol='codex_exec_resume', usage_scope='turn'))
    job = c.build_job(root, 1, role, [])
    first = execution.origin(root, 1)
    now = budget.clock_stamp()
    old = dict(at_unix=now['at_unix']-(now['at_boottime']+20 if closed_clock else 10),
        at_boottime=100., boot_id='original-host-boot')
    with monkeypatch.context() as previous_boot:
        previous_boot.setattr(budget, 'clock_stamp', lambda: dict(old))
        c.write(root/'host-started.json', old)
        budget.transition(root, 'advance', 'fixture_original_native')
        anchor = budget.accept_node(root, job)
        intent = dict(step=1, task_ref=c.record_ref(root/'jobs/001.json'),
            budget_slice=anchor, **profiles.binding(c.settings(root, job), job))
        accepted.accept_native(root, 1, first, intent)
        if after_wake:
            c.write(first/'usage.json', METER)
            report = dict(role='W', limits=anchor['limits'], advance_seconds=0.,
                observed_tokens=14, dispatches=1, usage_observed=True, **METER)
            c.write(first/'native-outcome.json', dict(returncode=0, stop_reason=None,
                terminal_observed=True, cleanup='observed_group_cleaned', budget_slice=report))
            accepted.accept_terminal(root, 1, first, meter=METER, unknown=False,
                metering=dict(coverage='terminal', reporting_delay_unmeasured=False,
                    inflight_usage_unknown=False))
            anchor = budget.accept_continuation(root, job, [first])
            record = first/'continuations/001'
            intent = dict(step=1, attempt=1, continuation_kind='execution_wake',
                task_ref=c.record_ref(root/'jobs/001.json'),
                root_intent_ref=c.record_ref(first/'native-intent.json'),
                previous=execution.proof(first), budget_slice=anchor,
                session=dict(thread_id=THREAD, home=str(root/'native-homes/001'),
                    workspace=str(c.workspace(root, 1))),
                **profiles.binding(c.settings(root, job), job))
            accepted.accept_native(root, 1, record, intent)
        else:
            record = first
        binding = execution.identity(root, 1)
        for name in ('runner-process.json', 'native-process.json'):
            c.write(record/name, dict(pid=999999, boot_id=old['boot_id'], start_ticks=1,
                pgid=999999, sid=999999, ppid=1, task_ref=binding))
        (record/'native.jsonl').write_text(json.dumps(dict(type='thread.started', thread_id=THREAD))+'\n')
        if meter:
            c.write(record/'usage.json', METER)
        if closed_clock:
            stopped = dict(old, at_unix=old['at_unix']+10, at_boottime=110.)
            previous_boot.setattr(budget, 'clock_stamp', lambda: dict(stopped))
            budget.transition(root, 'paused', 'fixture_old_controller_stopped')
    home = root/'native-homes/001'
    session = home/'sessions/original.jsonl'
    session.parent.mkdir(parents=True)
    session.write_text(json.dumps(dict(type='session_meta', payload=dict(
        id=THREAD, cwd=str(c.workspace(root, 1)))))+'\n')
    (c.workspace(root, 1)/'work').mkdir(exist_ok=True)
    (c.workspace(root, 1)/'work/checkpoint').write_text('original checkpoint')
    originals = [root/'settings.json', root/'jobs/001.json', session,
        record/'native-intent.json', record/'runner-process.json',
        record/'native-process.json', record/'native.jsonl']
    if meter:
        originals.append(record/'usage.json')
    return root, record, {path: path.read_bytes() for path in originals}


@pytest.mark.parametrize('after_wake', [False, True], ids=['original', 'execution-wake'])
@pytest.mark.parametrize('meter', [False, True], ids=['unreported', 'observed-floor'])
def test_cross_boot_reentry_accepts_once_and_closes_interrupted_accounting(
        tmp_path, monkeypatch, after_wake, meter):
    root, prior, originals = accepted_interrupted_native(tmp_path, monkeypatch,
        after_wake=after_wake, meter=meter)
    starts = []
    monkeypatch.setattr(runtime, 'start', lambda *args: starts.append(args))
    assert runtime.node_state(root, 1) == 'interrupted'
    before = accepted.read(root)
    before_usage = accepted.usage(root, head=before)
    intent = continuation.reenter(root, 1)
    current = execution.record(root, 1)
    head = accepted.read(root)
    previous = accepted.native_attempt(root, 1, prior, head=head)
    assert previous['status'] == 'closed' and previous['unknown'] is True
    assert previous['meter'] == (METER if meter else None)
    assert accepted.native_attempt(root, 1, current, head=head)['status'] == 'open'
    assert head['current_native']['record'] == current.relative_to(root).as_posix()
    assert head['dispatch_count'] == before['dispatch_count']+1
    assert head['continuation_count'] == before['continuation_count']
    assert intent['budget_slice']['previous_tokens'] == (14 if after_wake else 0)+(14 if meter else 0)
    assert intent['session']['thread_id'] == THREAD
    assert not (prior/'native-outcome.json').exists()
    if not meter:
        assert not (prior/'usage.json').exists()
    assert all(path.read_bytes() == value for path, value in originals.items())
    # No running invocation is reported as zero: only the predecessor's
    # observed lower bound moves into cumulative accepted billing.
    usage = accepted.usage(root, head=head)
    assert usage['observed_tokens'] == before_usage['observed_tokens']
    assert usage['unknown_steps'] == [1] and usage['blocking_unknown_count'] == 0
    assert budget.used_steps(root) == (2 if after_wake else 1)
    assert starts == [(root, 1, 2 if after_wake else 1)]
    assert continuation.reenter(root, 1) == intent
    assert accepted.read(root)['seq'] == head['seq']
    assert starts == [(root, 1, 2 if after_wake else 1)]


def test_failed_reentry_publication_keeps_intent_unpublished_and_settlement_idempotent(tmp_path, monkeypatch):
    root, prior, originals = accepted_interrupted_native(tmp_path, monkeypatch)
    before = accepted.read(root)
    starts = []
    monkeypatch.setattr(runtime, 'start', lambda *args: starts.append(args))
    publish = accepted.atomic_files.publish_bytes

    def interrupted_publication(path, payload):
        if path.parent == root/'control-ledger' and json.loads(payload).get('kind') == 'native_accept':
            raise OSError('fixture interrupted native acceptance')
        return publish(path, payload)

    with monkeypatch.context() as crash:
        crash.setattr(accepted.atomic_files, 'publish_bytes', interrupted_publication)
        with pytest.raises(OSError, match='fixture interrupted native acceptance'):
            continuation.reenter(root, 1)
    pending = prior/'continuations/001'
    assert not (pending/'native-intent.json').exists()
    assert execution.records(root, 1) == [prior]
    head = accepted.read(root)
    assert head['current_native'] is None
    assert head['dispatch_count'] == before['dispatch_count']
    assert accepted.native_attempt(root, 1, prior, head=head)['status'] == 'closed'
    assert head['usage']['input_tokens'] == METER['input_tokens']
    assert starts == []
    continuation.reenter(root, 1)
    after = accepted.read(root)
    assert after['usage'] == head['usage']
    assert after['dispatch_count'] == before['dispatch_count']+1
    assert after['current_native']['record'] == pending.relative_to(root).as_posix()
    assert starts == [(root, 1, 1)]
    assert all(path.read_bytes() == value for path, value in originals.items())


def test_unaccepted_intent_remains_a_rejected_original(tmp_path, monkeypatch):
    root, prior, _ = accepted_interrupted_native(tmp_path, monkeypatch)
    orphan = prior/'continuations/001/native-intent.json'
    c.write(orphan, dict(attempt=1, task_ref=c.record_ref(root/'jobs/001.json')))
    with pytest.raises(ValueError, match='Original native intent has not been accepted'):
        execution.records(root, 1)


@pytest.mark.process_e2e
def test_supported_run_collects_same_session_and_next_native_with_ledger_publication(tmp_path, monkeypatch):
    from test_worker_resume import FAKE, wait_runner
    root, prior, originals = accepted_interrupted_native(tmp_path, monkeypatch,
        role='H', fake=FAKE, closed_clock=True)
    assert budget.snapshot(root)['consumed_seconds'] == 10.
    result = controller.run(root)
    assert result['status'] == 'final_a_completed', result
    wait_runner(root, 1)
    wait_runner(root, 2)
    current = execution.record(root, 1)
    assert c.read(current/'native-intent.json')['session']['thread_id'] == THREAD
    assert (c.workspace(root, 1)/'work/launches').read_text() == 'resume\n'
    assert (c.workspace(root, 2)/'work/launches').read_text() == 'exec\n'
    assert not (prior/'native-outcome.json').exists()
    assert accepted.native_attempt(root, 1, prior)['unknown'] is True
    assert accepted.usage(root)['observed_tokens'] == 58
    assert budget.used_steps(root) == 2
    assert all(path.read_bytes() == value for path, value in originals.items())
    assert controller.run(root)['status'] == 'final_a_completed'
