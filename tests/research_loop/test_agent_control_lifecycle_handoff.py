"""Exact lifecycle handoffs through production collectors, without models."""
import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, execution, native, runtime
from test_final_a_completion_contract import complete
from test_native_transport_stall import _scene as native_scene


def _scene(tmp_path, monkeypatch):
    root, record = native_scene(tmp_path, monkeypatch)
    accepted_budget.accept_native(root, 1, record, c.read(record/'native-intent.json'))
    return root, record


def interrupted_original(tmp_path, monkeypatch):
    from test_reboot_reentry import interrupted_instance
    from pathlib import Path
    write = c.write
    def publish_intent(path, value):
        result = write(path, value)
        path = Path(path)
        if path.name == 'native-intent.json' and path.parent.name == '001':
            # The closed-clock fixture records ten consumed seconds on its old
            # boot. Accept the original there, before returning to this boot.
            root = path.parents[2]
            started = c.read(root/'host-started.json')
            stamp = dict(started, at_unix=started['at_unix']+10,
                at_boottime=started['at_boottime']+10)
            with monkeypatch.context() as original_clock:
                original_clock.setattr(budget, 'clock_stamp', lambda: stamp)
                accepted_budget.accept_native(root, 1, path.parent, value)
        return result
    with monkeypatch.context() as publication:
        publication.setattr(c, 'write', publish_intent)
        root, saved = interrupted_instance(tmp_path, monkeypatch, closed_clock=True)
    return root, saved


@pytest.mark.parametrize('reason', ['wall_limit', 'token_limit'])
@pytest.mark.parametrize('entry', ['run', 'continue'])
def test_prelaunch_budget_refusal_reaches_final_with_original_facts(tmp_path, monkeypatch, reason, entry):
    root, record = _scene(tmp_path, monkeypatch)
    original_intent = (record / 'native-intent.json').read_bytes()
    snapshot = budget._snapshot

    def exhausted(*args, **kwargs):
        value = dict(snapshot(*args, **kwargs))
        value['seconds_remaining' if reason == 'wall_limit' else 'max_tokens'] = 0
        return value

    def forbidden(*args, **kwargs):
        pytest.fail('A pre-submission refusal cannot launch native work')

    with monkeypatch.context() as patch:
        patch.setattr(budget, '_snapshot', exhausted)
        patch.setattr(native.subprocess, 'Popen', forbidden)
        native.run_native(root, 1)
    outcome = c.read(record / 'native-outcome.json')
    assert outcome['native_started'] is False and outcome['returncode'] is None
    assert outcome['stop_reason'] == reason
    stopped_bytes = {path: path.read_bytes() for path in (
        record / 'native-intent.json', record / 'native-outcome.json', record / 'usage.json')}
    assert budget.prelaunch_budget_reason(root, 1, record) == reason
    stopped = budget.stopped_report_original(root, 1)
    assert set(stopped['records']) == {'native-intent.json', 'native-outcome.json', 'usage.json'}

    launches = []

    def finish_original_final(actual_root, step, attempt=0):
        from scidiscovery.research_loop import backends
        assert actual_root == root and step == 2 and attempt == 0
        launches.append(step)
        job = c.read(root / 'jobs/002.json')
        assert job['final_report']['stopped_original'] == stopped
        assert job['direct_refs'] == []
        _, final_outcome = complete(root, job, c.read(execution.origin(root, step) / 'native-intent.json'))
        final_record = execution.origin(root, step)
        backend = backends.selected(c.settings(root, job))
        final_outcome['native_backend'] = backend.name
        final_outcome['metering']['source'] = backend.terminal_source
        c.write(final_record / 'usage-observation.json', final_outcome['metering'])
        c.write(final_record / 'native-outcome.json', final_outcome)

    monkeypatch.setattr(runtime, 'start', finish_original_final)
    # Explicit continue enters the same retained-original run collector.
    if entry == 'continue':
        from scidiscovery.research_loop import instance_control
        result = instance_control.continue_instance(root)
    else:
        result = controller.run(root)
    assert result['status'] == 'final_a_completed', (result.get('status'), result.get('reason'), result)
    begin = budget.closeout_state(root)['begin']
    assert begin['stop_reason'] == reason and begin['stopped_original'] == stopped
    claim = budget.final_a_barrier(root)
    assert claim['step'] == 2 and launches == [2]
    assert (record / 'native-intent.json').read_bytes() == original_intent
    assert all(path.read_bytes() == data for path, data in stopped_bytes.items())
    assert not (record / 'result-identity.json').exists()
    assert not (record / 'native-process.json').exists()
    assert not (root / 'jobs/003.json').exists()
    usage = native.usage(root)
    assert usage['observed_tokens'] == 1210
    assert controller.run(root)['status'] == 'final_a_completed'
    assert native.usage(root) == usage and budget.final_a_barrier(root) == claim
    assert launches == [2]


@pytest.mark.parametrize('change', ['unknown', 'started', 'meter', 'reason', 'identity'])
def test_incomplete_or_unknown_submission_never_becomes_budget_refusal(tmp_path, monkeypatch, change):
    root, record = _scene(tmp_path, monkeypatch)
    outcome = dict(returncode=None, native_started=False, stop_reason='wall_limit',
        thread_id=None, usage=native.usage(root),
        limitation='Budget stopped the original intent before native submission.')
    c.write(record / 'usage.json', dict(input_tokens=0, cached_input_tokens=0, output_tokens=0))
    if change == 'unknown':outcome['limitation'] = 'Original submission response is unknown'
    elif change == 'started':outcome['native_started'] = True
    elif change == 'meter':c.write(record / 'usage.json', dict(input_tokens=1, cached_input_tokens=0, output_tokens=0))
    elif change == 'reason':outcome['stop_reason'] = 'native_error_unknown'
    else:
        intent = c.read(record / 'native-intent.json')
        c.write(record / 'native-intent.json', dict(intent, task_ref=dict(path='/different/task.json')))
    c.write(record / 'native-outcome.json', outcome)
    assert budget.prelaunch_budget_reason(root, 1, record) is None
    with pytest.raises((ValueError, OSError)):
        budget.stopped_report_original(root, 1)
    assert budget.final_a_barrier(root) is None
    assert not (root / 'jobs/002.json').exists()


def test_accepted_reentry_prelaunch_refusal_reaches_final_without_refunding_original(tmp_path, monkeypatch):
    from scidiscovery.research_loop import continuation
    root, saved = interrupted_original(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, 'start', lambda *args: None)
    with controller._writer(root):
        accepted = continuation.reenter(root, 1, explicit=True, expected_attempt=0,
            native_intent_ref=c.record_ref(execution.origin(root, 1)/'native-intent.json'), new_context=True)
    refused = execution.record(root, 1)
    assert accepted['budget_slice']['previous_tokens'] == 14
    snapshot = budget._snapshot

    def exhausted(*args, **kwargs):
        value = dict(snapshot(*args, **kwargs));value['seconds_remaining'] = 0.
        return value

    with monkeypatch.context() as patch:
        patch.setattr(budget, '_snapshot', exhausted)
        patch.setattr(native.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('Refused reentry cannot submit'))
        native.run_native(root, 1, 1)
    stopped = budget.stopped_report_original(root, 1)
    assert stopped['records']['continuations/001/native-intent.json'] == c.record_ref(refused / 'native-intent.json')
    assert stopped['records']['usage.json'] == c.record_ref(execution.origin(root, 1) / 'usage.json')
    retained = {path: path.read_bytes() for path in (
        refused / 'native-intent.json', refused / 'native-outcome.json', refused / 'usage.json')}
    launches = []

    def final_return(actual_root, step, attempt=0):
        assert actual_root == root and step == 2 and attempt == 0
        launches.append(step)
        job = c.read(root / 'jobs/002.json')
        assert job['final_report']['stopped_original'] == stopped
        complete(root, job, c.read(execution.origin(root, step) / 'native-intent.json'))

    monkeypatch.setattr(runtime, 'start', final_return)
    result = controller.run(root)
    assert result['status'] == 'final_a_completed', (result.get('status'), result.get('reason'), result)
    assert native.usage(root)['observed_tokens'] == 1224
    assert launches == [2] and len(execution.records(root, 1)) == 2
    assert all(path.read_bytes() == data for path, data in {**saved, **retained}.items())
    assert budget.closeout_state(root)['begin']['stopped_original'] == stopped
    assert not (root / 'jobs/003.json').exists()


def test_prelaunch_refusal_does_not_hide_an_unknown_earlier_attempt(tmp_path, monkeypatch):
    from scidiscovery.research_loop import continuation
    root, _ = interrupted_original(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, 'start', lambda *args: None)
    with controller._writer(root):
        continuation.reenter(root, 1, explicit=True, expected_attempt=0,
            native_intent_ref=c.record_ref(execution.origin(root, 1)/'native-intent.json'), new_context=True)
    record = execution.record(root, 1)
    c.write(record / 'usage.json', dict(input_tokens=0, cached_input_tokens=0, output_tokens=0))
    c.write(record / 'native-outcome.json', dict(returncode=None, native_started=False, stop_reason='wall_limit',
        thread_id=None, usage=native.usage(root), limitation='Budget stopped the original intent before native submission.'))
    origin = execution.origin(root, 1)
    # An actually unknown earlier submission remains unknown. The last host's
    # positive refusal cannot upgrade or replay that earlier invocation.
    c.write(origin / 'native-outcome.json', dict(returncode=None, native_started=True,
        stop_reason='native_error_unknown', cleanup='observed_group_cleaned', thread_id=None))
    before = (origin / 'native-outcome.json').read_bytes()
    with pytest.raises((ValueError, OSError, continuation.ResumeUnavailable)):
        budget.stopped_report_original(root, 1)
    assert (origin / 'native-outcome.json').read_bytes() == before
    assert budget.final_a_barrier(root) is None and not (root / 'jobs/002.json').exists()
