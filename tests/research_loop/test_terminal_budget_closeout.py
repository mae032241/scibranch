"""Collect completed W originals before the existing finite FINAL boundary."""
import json
import sys

import pytest

from scidiscovery.research_loop import (accepted_budget, budget, contracts as c,
    controller, execution, native, runtime)
from test_final_a_completion_contract import complete
from test_history_handoff import finish


def completed_worker(tmp_path, monkeypatch, *, boundary='tokens'):
    if boundary == 'wall':
        fixture_clock = [budget.clock_stamp()]
        monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(fixture_clock[0]))
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain completed observations and report all unfinished scientific work.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, [sys.executable], max_tokens=40 if boundary == 'tokens' else 1000,
        max_seconds=120, max_steps=12, model='fixture-no-model', reasoning_effort='high',
        search_policy=None, node_budget_mode='soft',
        node_budget_policy={role: dict(max_observed_tokens=20, max_seconds=20)
                            for role in 'HWAE'},
        resume_policy=dict(max_attempts=1, protocol='codex_exec_resume', usage_scope='turn',
            output_repair=True), worker_fragment_policy=dict(context='same_session'))
    finish(root, 1, 'H', [], action='experiment')
    job = c.build_job(root, 2, 'W', [1])
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', 'Retained terminal transport fixture')
    record = execution.origin(root, 2)
    intent = dict(task_ref=c.record_ref(root / 'jobs/002.json'),
        budget_slice=budget.accept_node(root, job))
    c.write(record / 'native-intent.json', intent)
    accepted_budget.accept_native(root, 2, record, intent)
    meter = dict(input_tokens=37, cached_input_tokens=30, output_tokens=3)
    c.write(record / 'science-output.json', dict(summary='The completed W comparison is retained.',
        scientific_result='Known original observations; required additional experiments remain unfinished.',
        limitations=['Deterministic transport fixture, without scientific qualification.'],
        stage_intent='review', consultation_question=(
            'Which retained observations address the remaining comparison, and which evidence is still missing?')))
    c.write(record / 'usage.json', meter)
    observation = dict(source='turn.completed', coverage='terminal', tokens=meter)
    c.write(record / 'usage-observation.json', observation)
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None,
        native_started=False, terminal_observed=True, cleanup='observed_group_cleaned',
        metering=observation, usage=native.usage(root),
        raw_ref=c.file_ref(record / 'science-output.json')))
    if boundary == 'wall':
        # Retain a forward-only elapsed active interval after W completed.
        # Keep that exact clock for collection and the final return as well.
        stamp = fixture_clock[0]
        fixture_clock[0] = dict(stamp, at_unix=stamp['at_unix']+1000,
            at_boottime=stamp['at_boottime']+1000)
        assert budget.snapshot(root)['seconds_remaining'] == 0

    launches = []
    def final_author(root, step):
        job = c.read(root / 'jobs' / f'{step:03d}.json')
        assert job['role'] == 'A' and job['final_only'] and step == 3
        launches.append(step)
        intent = dict(step=step, task_ref=c.record_ref(root / 'jobs/003.json'),
            budget_slice=budget.accept_node(root, job))
        c.write(execution.origin(root, step) / 'native-intent.json', intent)
        accepted_budget.accept_native(root, step, execution.origin(root, step), intent)
        complete(root, job, intent)
        return c.seal_result(root, step)
    def forbidden(*args, **kwargs):
        pytest.fail('Completed W collection must not start another native or scientific execution')
    monkeypatch.setattr(native, 'execute_native', final_author)
    monkeypatch.setattr(runtime, 'node_state', lambda *args: 'terminal')
    monkeypatch.setattr(runtime, 'start', forbidden)
    return root, record, launches


@pytest.mark.parametrize('command', ['run', 'worker_resume'])
@pytest.mark.parametrize('boundary', ['tokens', 'wall'])
def test_completed_valid_w_over_shared_cap_seals_then_returns_one_final_author(tmp_path, monkeypatch, command, boundary):
    root, record, launches = completed_worker(tmp_path, monkeypatch, boundary=boundary)
    originals = {path: path.read_bytes() for path in (
        root / 'settings.json', root / 'jobs/002.json', record / 'native-intent.json',
        record / 'native-outcome.json', record / 'science-output.json', record / 'usage.json')}
    meter = native.usage(root)
    limits = budget.snapshot(root)
    assert meter['observed_tokens'] == meter['accounted_tokens'] == 50
    if boundary == 'tokens':
        assert limits['max_tokens'] == 40 < meter['accounted_tokens']
        assert limits['seconds_remaining'] > 0
    else:
        assert limits['max_tokens'] == 1000 > meter['accounted_tokens']
        assert limits['seconds_remaining'] == 0
    result = (controller.run(root) if command == 'run' else controller.worker_resume(root, 2))
    assert result['status'] == 'final_a_completed', json.dumps(result, indent=2)
    assert (record / 'result-identity.json').is_file()
    assert c.read(record / 'stage-end.json')['reason'] == 'worker_review'
    assert budget.closeout_state(root)['begin']['stop_reason'] == (
        'closeout_reserve_tokens' if boundary == 'tokens' else 'closeout_reserve_seconds')
    assert c.validate(c.workspace(root, 2))['stage_intent'] == 'review'
    final_job = c.read(root / 'jobs/003.json')
    assert any(ref['path'] == 'inputs/parents/002/conclusion.json' for ref in final_job['direct_refs'])
    assert final_job['final_report']['source_cutoff'] == 2
    assert budget.final_a_barrier(root)['step'] == 3
    assert launches == [3] and not (record / 'continuations').exists()
    assert not (root / 'jobs/004.json').exists()
    assert all(path.read_bytes() == original for path, original in originals.items())
    sealed = {path: path.read_bytes() for path in (record / 'result-identity.json', record / 'stage-end.json')}
    meter = native.usage(root)
    assert controller.run(root)['status'] == 'final_a_completed'
    assert controller.worker_resume(root, 2)['status'] == 'final_a_completed'
    assert launches == [3] and native.usage(root) == meter
    assert all(path.read_bytes() == original for path, original in {**originals, **sealed}.items())
    assert not any(event['kind'] == 'budget_amend' for event in budget._events(root)[0])
