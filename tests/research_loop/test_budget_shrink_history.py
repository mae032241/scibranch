"""A reduced future step ceiling never erases retained native consumption."""
import json
import sys
from pathlib import Path
from urllib.parse import urlencode

import pytest

from scidiscovery.research_loop import (backends, budget, checkpoint, contracts as c, continuation,
    controller, execution, instance_archive as archive, instance_control as control,
    instance_science as science, native, runtime)
from scidiscovery.research_loop.instance_ui import InstanceUI
from test_composed_execution_results import composed_registry


def prepare(tmp_path, *, fragments=False):
    public = tmp_path / 'public';public.mkdir()
    objective = public / 'objective.md';objective.write_text('Retain original science and consumption after reducing future work.')
    root = tmp_path / 'instance'
    options = dict(node_budget_policy={role: dict(max_observed_tokens=100, max_seconds=20) for role in 'HWAE'},
        resume_policy=dict(max_attempts=2, protocol='codex_exec_resume', usage_scope='turn'),
        worker_fragment_policy=dict(context='new_context')) if fragments else {}
    controller.prepare(root, objective, public, [sys.executable], max_tokens=2000, max_seconds=200,
        max_steps=5, model='fixture', reasoning_effort='high', search_policy=None, **options)
    return root


def finish(root, step, role, parents, *, action='wait', instruction='', artifact=False, seal=True, job=None, anchor=None):
    if job is None:job = c.build_job(root, step, role, parents, instruction=instruction)
    record = execution.origin(root, step)
    intent = dict(step=step, task_sha256=c.digest(root / 'jobs' / f'{step:03d}.json'))
    if anchor is not None:intent['budget_slice'] = anchor
    c.write(record / 'native-intent.json', intent)
    payload = dict(summary='Fixture contribution', scientific_result='Retain this original contribution with its limits.',
        limitations=['Synthetic native transport; no model or scientific experiment'], implementation_selection=None)
    if role == 'W':payload.update(stage_intent='review', mechanism_findings=None)
    if role == 'H':payload['search_plan'] = None
    if role == 'A':payload.update(search_review=None, candidate_recommendation=None, progress_assessment=None,
        decision_request=None, portfolio_review=None)
    if role in {'H', 'A'}:payload.update(next_action=action, route_reason='Review the retained scientific contribution.')
    if artifact:(c.workspace(root, step) / 'artifacts/result.txt').write_text('Exact original A deliverable.\n')
    c.write(record / 'science-output.json', payload)
    meter = dict(input_tokens=7, cached_input_tokens=2, output_tokens=3)
    c.write(record / 'usage.json', meter)
    outcome = dict(returncode=0, stop_reason=None, cleanup='observed_group_cleaned',
        usage=native.usage(root), raw_sha256=c.digest(record / 'science-output.json'))
    if (anchor or {}).get('final_return') is not None:
        # Simulated terminal transport still needs the exact real FINAL receipt;
        # it grants no scientific qualification to this fixture contribution.
        backend = backends.selected(c.settings(root, job))
        outcome.update(native_backend=backend.name,
            metering=dict(coverage='terminal', reason=None, source=backend.terminal_source, tokens=meter))
        accepted = budget.final_a_return(root, job, intent)
        assert accepted is not None
        outcome['final_a_completion'] = budget.final_a_receipt(accepted, intent, meter, outcome['usage'])
        c.write(record / 'usage-observation.json', outcome['metering'])
    c.write(record / 'native-outcome.json', outcome)
    if seal:c.seal_result(root, step)
    return job


def three(root, *, last_sealed=True):
    job = finish(root, 1, 'H', [], action='experiment')
    route = controller._route(root, 1, 'H', c.validate(c.workspace(root, 1)), job, None, None)
    job = finish(root, 2, 'W', route[1], instruction=route[2])
    route = controller._route(root, 2, 'W', c.validate(c.workspace(root, 2)), job, 1, None)
    return finish(root, 3, 'A', route[1], action='continue_w', instruction=route[2], artifact=True, seal=last_sealed)


def shrink(root):
    return budget.amend(root, max_steps=2, authorization_note='Reduce future work without refunding originals.',
        authorization_statement='Keep all original results and consumption.')


def originals(root):
    return {path: path.read_bytes() for folder in ('jobs', 'native-evidence', 'project', 'host-history')
        for path in (root / folder).rglob('*') if path.is_file() and path.name != 'runner.lock'}


def assert_originals(values):
    assert all(path.read_bytes() == value for path, value in values.items())


def test_shrink_keeps_sealed_third_subject_reads_and_allows_only_one_final_a(tmp_path, monkeypatch):
    root = prepare(tmp_path);three(root)
    package = tmp_path / 'archive';archive.archive_export(root, package)
    shrink(root)
    assert budget.effective_max_steps(root) == 2 and budget.historical_step_limit(root) == 5
    assert native.usage(root)['observed_tokens'] == 30
    assert native.usage(root)['blocking_unknown_steps'] == []
    retained = originals(root)
    def forbidden(*args, **kwargs):pytest.fail('Read/restore cannot sample current consumption or dispatch native work')
    with monkeypatch.context() as patch:
        patch.setattr(native, 'execute_native', forbidden);patch.setattr(runtime, 'start', forbidden)
        with science.science(root, 3) as result:
            assert json.load(result.stream)['scientific_result'] == 'Retain this original contribution with its limits.'
        member = science.materials(root, 3)['items'][0]
        material = c.workspace(root, 3) / 'artifacts/result.txt';raw = material.read_bytes();material.unlink()
        ledger = {path: path.read_bytes() for path in (root / 'control-ledger').glob('*.json')}
        with monkeypatch.context() as restore_patch:
            restore_patch.setattr(budget, 'snapshot', forbidden);restore_patch.setattr(budget, 'clock_stamp', forbidden)
            assert archive.archive_restore_material(root, package, step=3, index=member['index'])['status'] == 'material_restored'
        assert material.read_bytes() == raw
        assert all(path.read_bytes() == value for path, value in ledger.items())
        view = control.status(root, limit=2, live_feedback=False)
        assert [row['step'] for row in view['nodes']] == [1, 2] and view['next_after'] == 2
        assert [row['step'] for row in control.status(root, after=2, limit=2, live_feedback=False)['nodes']] == [3]
        assert control.status(root, live_feedback=False)['budget']['max_steps'] == 2
        key = tmp_path / 'ui.key';key.write_bytes(b'fixture-only-local-ui-secret-1234567890');key.chmod(0o600)
        ui = InstanceUI(root, secret_file=key);ui.host = '127.0.0.1:18753'
        _, _, headers = ui.get('/view?' + urlencode(dict(token=ui.read_token)), host=ui.host)
        assert b'/results/3' in ui.get('/results', host=ui.host, cookie=headers['Set-Cookie'])[1]
        assert ui.get('/results/3', host=ui.host, cookie=headers['Set-Cookie'])[0] == 200
    assert_originals(retained)
    launches = []
    def final_only(selected, step):
        assert selected == root and step == 4
        job = c.read(root / 'jobs/004.json')
        assert job['role'] == 'A' and job['final_only'] is True
        launches.append(step)
        finish(root, step, 'A', [], action='finish', job=job, anchor=budget.accept_node(root, job))
    with monkeypatch.context() as patch:
        patch.setattr(native, 'execute_native', final_only)
        result = controller.run(root)
        assert result['status'] == 'final_a_completed'
        assert controller.run(root)['status'] == 'final_a_completed'
    assert launches == [4] and native.usage(root)['observed_tokens'] == 40
    assert not (root / 'jobs/005.json').exists()
    assert_originals(retained)
    # FINAL rejects the next ordinary task before a job or native intent exists.
    with pytest.raises(ValueError, match='Final A return permits no further scientific task'):
        c.build_job(root, 5, 'W', [1, 2, 3], instruction='No new ordinary work is authorized.')
    assert not (root / 'jobs/005.json').exists()
    assert not budget.task_step_available(root, 5)
    assert not (execution.origin(root, 5) / 'native-intent.json').exists()
    assert_originals(retained)


def test_shrink_retains_unknown_meter_even_if_original_job_is_later_missing(tmp_path):
    root = prepare(tmp_path);three(root, last_sealed=False)
    record = execution.origin(root, 3)
    meter = dict(input_tokens=11, cached_input_tokens=2, output_tokens=6)
    c.write(record / 'usage.json', meter)
    metadata = dict(coverage='unknown', reason='terminal_usage_unreported', tokens=meter)
    c.write(record / 'usage-observation.json', metadata)
    c.write(record / 'native-outcome.json', dict(returncode=-15, stop_reason='token_limit',
        cleanup='observed_group_cleaned', metering=metadata))
    shrink(root)
    # Losing this job is an incomplete historical subject, never a new execution grant.
    (root / 'jobs/003.json').unlink()
    retained = originals(root)
    result = native.usage(root)
    assert result['observed_tokens'] == result['accounted_tokens'] == 37
    assert result['unknown_steps'] == [3]
    # Protected FINAL preserves billing uncertainty without a synthetic charge;
    # the missing original job independently blocks execution and replay.
    assert result['blocking_unknown_steps'] == []
    assert budget.execution_blocked(root)
    assert_originals(retained)


def test_previously_accepted_fragment_chain_survives_shrink_but_next_turn_does_not_fit(tmp_path):
    root = prepare(tmp_path, fragments=True)
    job = c.build_job(root, 1, 'W', [])
    first = execution.origin(root, 1);paths = []
    for number in range(3):
        anchor = budget.accept_node(root, job) if number == 0 else budget.accept_continuation(root, job, paths)
        record = first if number == 0 else first / 'continuations' / f'{number:03d}'
        record.mkdir(parents=True, exist_ok=True)
        intent = dict(step=1, task_sha256=c.digest(root / 'jobs/001.json'), budget_slice=anchor)
        if number:
            prior = paths[-1];proof = prior / 'checkpoint.json'
            intent.update(attempt=number, continuation_kind='fragment', context_mode='new_context',
                native_home=str(root / 'native-homes/001' / f'context-{number:03d}'),
                root_intent_sha256=c.digest(first / 'native-intent.json'), previous=execution.proof(prior),
                fragment_checkpoint=dict(path=proof.relative_to(root).as_posix(), sha256=c.digest(proof)))
        c.write(record / 'native-intent.json', intent)
        meter = dict(input_tokens=7, cached_input_tokens=2, output_tokens=3)
        c.write(record / 'usage.json', meter)
        c.write(record / 'science-output.json', dict(summary='Native fragment fixture',
            scientific_result='Retain the checkpoint; no model was called.', limitations=['Synthetic native originals'],
            implementation_selection=None, stage_intent='continue', mechanism_findings=None))
        report = dict(budget.node_report(root, job, intent, meter), usage_observed=True)
        c.write(record / 'native-outcome.json', dict(returncode=7 if number == 2 else 0, stop_reason=None,
            cleanup='observed_group_cleaned', usage=native.usage(root), budget_slice=report,
            raw_sha256=c.digest(record / 'science-output.json')))
        checkpoint.capture(root, job, record)
        paths.append(record)
    shrink(root)
    retained = originals(root)
    assert execution.records(root, 1) == paths
    continuation.validate_fragment_chain(root, 1, paths)
    assert native.usage(root)['observed_tokens'] == 30 and budget.used_steps(root) == 3
    with pytest.raises(budget.NodeBudgetUnavailable) as stopped:
        budget.accept_continuation(root, job, paths)
    assert stopped.value.reason == 'worker_resume_budget_unavailable'
    assert not (first / 'continuations/003').exists()
    assert_originals(retained)


def test_later_original_successor_above_initial_ceiling_blocks_resume_after_amendment(tmp_path, monkeypatch):
    from test_worker_resume import instance, fail
    root = instance(tmp_path, steps=5);first = fail(root)
    budget.amend(root, max_steps=8, authorization_note='A finite expanded review window.',
        authorization_statement='Retain prior work and permit bounded follow-up.')
    # An unreconciled successor intent is sufficient to prohibit another native
    # turn for this old stage; it does not assert a scientific result or verdict.
    later = execution.origin(root, 6);later.mkdir(parents=True)
    c.write(later / 'native-intent.json', dict(step=6, task_sha256='0' * 64))
    retained = originals(root)
    def forbidden(*args, **kwargs):pytest.fail('A retained successor must prevent a new native submission')
    from scidiscovery.research_loop import runtime
    monkeypatch.setattr(runtime, 'start', forbidden)
    with pytest.raises(continuation.ResumeUnavailable, match='worker_resume_successor_present'):
        continuation.start(root, 1)
    assert (c.workspace(root, 1) / 'work/launches').read_text() == 'exec\n'
    assert not (first / 'continuations/001').exists()
    assert_originals(retained)


def test_historical_scope_retains_peak_grant_without_sampling_and_rejects_changed_chain(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    budget.amend(root, max_steps=24, authorization_note='Finite expanded review scope.',
        authorization_statement='Retain the exact original work.')
    shrink(root)
    retained = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    def forbidden(*args, **kwargs):pytest.fail('Historical authorization reads cannot sample time or consumption')
    monkeypatch.setattr(budget, 'snapshot', forbidden)
    monkeypatch.setattr(budget, 'clock_stamp', forbidden)
    monkeypatch.setattr(native, 'usage', forbidden)
    assert budget.effective_max_steps(root) == 2
    assert budget.historical_step_limit(root) == 24
    assert {path: path.read_bytes() for path in root.rglob('*') if path.is_file()} == retained
    path = root / 'control-ledger/000002.json';value = c.read(path)
    value['before_max_steps'] = 23;value['max_steps_delta'] = value['after_max_steps'] - 23
    c.write(path, value)
    with pytest.raises(ValueError, match='Budget step amendment predecessor changed'):
        budget.historical_step_limit(root)


@pytest.mark.process_e2e
def test_reconciled_waiting_wake_retains_successor_guard_above_initial_step_ceiling(tmp_path, composed_registry, monkeypatch):
    from test_managed_execution_wait import instance, stop_at_waiting
    from scidiscovery.research_loop import experiments as e, managed_wait, runtime
    root = instance(tmp_path, dict(collect=False, terminal_status='known_success'))
    config, call = stop_at_waiting(root)
    adapter = e.load(config);adapter.complete(call)
    observed = e.record_observation(config, adapter, call)
    assert observed['status'] == 'known_success'
    record = execution.origin(root, 2);job = c.read(root / 'jobs/002.json')
    waiting = managed_wait.validate(root, job, record)
    assert len(waiting['calls']) == 1 and len(e.Records(config).calls()) == 1
    observation = e.proof(e.Records(config).directory(call) / 'execution-observation.json')
    e.verify_observation(c.read(Path(observation['path'])),
        lambda item: Path(item['path']), interface=config['execution_interface'])
    assert c.settings(root)['max_steps'] == 7
    budget.amend(root, max_steps=10, authorization_note='Finite follow-up scope with original waiting result retained.',
        authorization_statement='Preserve the original calls and complete bounded follow-up.')
    # An unreconciled higher successor remains a blocker; no scientific result
    # or fresh execution permission is inferred from this retained intent.
    later = execution.origin(root, 8);later.mkdir(parents=True)
    c.write(later / 'native-intent.json', dict(step=8, task_sha256='0' * 64))
    retained = originals(root)
    ledger = {path: path.read_bytes() for path in (root / 'control-ledger').glob('*.json')}
    def forbidden(*args, **kwargs):pytest.fail('A retained successor must prevent a fresh execution-wake native turn')
    monkeypatch.setattr(runtime, 'start', forbidden)
    with pytest.raises(continuation.ResumeUnavailable, match='worker_resume_successor_present'):
        continuation.wake_start(root, 2, [observation])
    assert (c.workspace(root, 2) / 'work/launches').read_text() == 'exec\n'
    assert not (record / 'continuations/001').exists()
    assert len(e.Records(config).calls()) == 1
    assert all(path.read_bytes() == value for path, value in ledger.items())
    assert_originals(retained)
