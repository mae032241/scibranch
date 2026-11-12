"""Ordinary closeout diagnostics never authorize a replacement native task."""
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts, controller
from scidiscovery.research_loop import correction, engineering, fork_runtime

_native_call = controller._native_call


@pytest.fixture
def closeout(tmp_path, monkeypatch):
    root = tmp_path / 'instance'
    record = root / 'native-evidence/001'
    contracts.write(root / 'jobs/001.json', dict(step=1, role='A', purpose='closeout',
        source_cutoff=0, direct_refs=[]))
    contracts.write(record / 'native-intent.json', dict(step=1))
    outcome = dict(returncode=1)
    contracts.write(record / 'native-outcome.json', outcome)
    begin = dict(mode='model', first_step=1, source_cutoff=0, stop_reason='scientific_deliver',
        preparation_seconds=0.)
    calls = []
    monkeypatch.setattr(budget, 'closeout_state', lambda root: dict(begin=begin))
    monkeypatch.setattr(budget, 'final_a_barrier', lambda root: None)
    monkeypatch.setattr(budget, 'scientific_steps', lambda root, before: [])
    monkeypatch.setattr(budget, 'historical_step_limit', lambda root: 1)
    monkeypatch.setattr(budget, 'task_step_available', lambda root, step: True)
    monkeypatch.setattr(budget, 'execution_blocked', lambda root: False)
    monkeypatch.setattr(budget, 'transition', lambda *args: None)
    monkeypatch.setattr(correction, 'gate', lambda root, step: None)
    monkeypatch.setattr(fork_runtime, 'bootstrap', lambda root: None)
    monkeypatch.setattr(fork_runtime, 'is_fork', lambda root: False)
    monkeypatch.setattr(controller.configuration_updates, 'for_new_job', lambda root, step: {})
    monkeypatch.setattr(controller.execution, 'record', lambda root, step: record)
    monkeypatch.setattr(controller.runtime, 'node_state', lambda root, step: 'terminal')
    monkeypatch.setattr(controller, '_closeout_funds', lambda *args, **kwargs: True)
    monkeypatch.setattr(engineering, 'sources', lambda root, step: {})
    monkeypatch.setattr(contracts, 'build_job', lambda *args, **kwargs: None)
    monkeypatch.setattr(controller, '_closeout_result', lambda root, status, **details: dict(status=status, **details))
    monkeypatch.setattr(controller, '_native_call', lambda *args, **kwargs: calls.append((args, kwargs)))
    return SimpleNamespace(root=root, record=record, outcome=outcome, begin=begin, calls=calls)


def test_running_original_is_collected_through_existing_output_repair_route(closeout, monkeypatch):
    monkeypatch.setattr(controller.runtime, 'node_state', lambda root, step: 'running')

    def collect(*args, **kwargs):
        closeout.calls.append((args, kwargs))
        raise controller.OutputRepairRequired('missing_output', 'Return the required scientific JSON')

    monkeypatch.setattr(controller, '_native_call', collect)
    result = controller._run_closeout(closeout.root)
    assert result == dict(status='output_repair_required', step=1, reason='missing_output')
    assert closeout.calls == [((closeout.root, 1), {})]


@pytest.mark.parametrize('launch', [False, True])
@pytest.mark.parametrize('repair_context', [False, True])
def test_resume_unavailable_only_means_output_repair_in_explicit_repair_context(closeout, monkeypatch, repair_context, launch):
    contracts.write(closeout.record / 'native-outcome.json', dict(returncode=0))
    if launch:
        (closeout.record / 'native-intent.json').unlink()
    error = controller.continuation.ResumeUnavailable(
        'worker_resume_limit' if repair_context else 'worker_fragment_checkpoint_unavailable')
    if repair_context:
        error.status = 'output_repair_unavailable'

    def unavailable(*args, **kwargs):
        closeout.calls.append((args, kwargs))
        raise error

    monkeypatch.setattr(controller, '_native_call', unavailable)
    result = controller._run_closeout(closeout.root)
    assert result['status'] == ('output_repair_unavailable' if repair_context else 'closeout_native_unsealed')
    assert result['reason'] == str(error) and result['step'] == 1
    assert len(closeout.calls) == 1 and closeout.calls[0][1] == (dict(launch=True) if launch else {})
    assert not (closeout.record / 'continuations').exists()


@pytest.mark.parametrize('launch', [False, True])
def test_original_integrity_value_error_is_not_relabelled_as_closeout_failure(closeout, monkeypatch, launch):
    contracts.write(closeout.record / 'native-outcome.json', dict(returncode=0))
    if launch:
        (closeout.record / 'native-intent.json').unlink()
    error = ValueError('Original process receipt binding changed')

    def invalid(*args, **kwargs):
        raise error

    monkeypatch.setattr(controller, '_native_call', invalid)
    with pytest.raises(ValueError) as caught:
        controller._run_closeout(closeout.root)
    assert caught.value is error


@pytest.mark.parametrize('damage', ['missing', 'changed'])
def test_initial_attempt_raw_integrity_failure_is_rejected_by_inventory(tmp_path, monkeypatch, damage):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain this exact original scientific output.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'],
        max_tokens=1000, max_seconds=100., max_steps=3, model='fixture',
        reasoning_effort='high', search_policy=None)
    job = contracts.build_job(root, 1, 'A', [])
    record = root / 'native-evidence/001'
    accepted_budget.accept_native(root, 1, record, dict(step=1,
        task_ref=contracts.record_ref(root / 'jobs/001.json')))
    raw = record / 'science-output.json'
    raw.write_text('{"fixture":"the exact original output"}\n')
    contracts.write(record / 'native-outcome.json', dict(returncode=0,
        native_started=False, cleanup='not_started', raw_ref=contracts.file_ref(raw),
        usage=dict(unknown_steps=[])))
    begin = dict(mode='model', first_step=1, source_cutoff=0,
        stop_reason='scientific_deliver', preparation_seconds=0.)
    monkeypatch.setattr(budget, 'closeout_state', lambda root: dict(begin=begin))
    monkeypatch.setattr(controller, '_terminal_success',
        lambda *args: pytest.fail('The collection gate must not inspect or suppress raw integrity errors'))
    if damage == 'missing':
        raw.unlink()
    else:
        raw.write_text('{"fixture":"a changed output with different size"}\n')
    with pytest.raises(ValueError) as caught:
        controller._run_closeout(root)
    assert str(caught.value) == ('Expected an original regular file' if damage == 'missing'
        else 'Terminal scientific output is unverified or changed')
    assert not (record / 'result-identity.json').exists()
    assert not (record / 'continuations').exists()


@pytest.mark.parametrize('arbitrated', [False, True])
def test_first_launch_failure_keeps_original_resume_guidance(closeout, monkeypatch, arbitrated):
    (closeout.record / 'native-intent.json').unlink()
    (closeout.record / 'native-outcome.json').unlink()
    monkeypatch.setattr(controller, '_native_call', _native_call)

    def execute(root, step):
        closeout.calls.append((root, step))
        contracts.write(closeout.record / 'native-intent.json', dict(step=step))
        contracts.write(closeout.record / 'native-outcome.json', dict(returncode=1))
        raise RuntimeError('Private native diagnostic /private/native-home/token')

    monkeypatch.setattr(controller.native, 'execute_native', execute)
    monkeypatch.setattr(controller.continuation, 'automatic_decision', lambda root, step:
        dict(action='stop', reason='automatic_recovery_final_boundary', source_record=str(closeout.record))
        if arbitrated else dict(action='none'))
    monkeypatch.setattr(controller, '_recovery_state', lambda root, step, selected:
        (closeout.record / 'automatic-recovery.json', {}))
    result = controller._run_closeout(closeout.root)
    assert result == dict(status='closeout_native_unsealed', step=1,
        reason='Original closeout native failed; explicit worker-resume required for the same original task')
    assert closeout.calls == [(closeout.root, 1)]
    assert not (closeout.record / 'continuations').exists()


def test_final_closeout_keeps_its_separate_protected_route(closeout, monkeypatch):
    closeout.begin['mode'] = 'final'
    final_calls = []
    monkeypatch.setattr(controller, '_run_final', lambda root, begin:
        final_calls.append((root, begin)) or dict(status='protected_final'))
    assert controller._run_closeout(closeout.root) == dict(status='protected_final')
    assert final_calls == [(closeout.root, closeout.begin)] and closeout.calls == []
