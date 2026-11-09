"""A reduced future step ceiling never erases retained native consumption."""
import json
import sys
from pathlib import Path
from urllib.parse import urlencode

import pytest

from scidiscovery.research_loop import (accepted_budget, backends, budget, contracts as c, continuation,
    controller, execution, instance_control as control,
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
        max_steps=5, model='fixture', reasoning_effort='high', search_policy=None,
        # This retained-history fixture intentionally exercises the H-W-A route,
        # not the current default W-first scientific strategy.
        research_strategy_version=1, supervision_policy=None, **options)
    return root


def finish(root, step, role, parents, *, action='wait', instruction='', artifact=False, seal=True, job=None, anchor=None):
    if job is None:job = c.build_job(root, step, role, parents, instruction=instruction)
    record = execution.origin(root, step)
    intent = dict(step=step, task_ref=c.record_ref(root / 'jobs' / f'{step:03d}.json'))
    if anchor is None:anchor = budget.accept_node(root, job)
    if anchor is not None:intent['budget_slice'] = anchor
    accepted_budget.accept_native(root, step, record, intent)
    payload = dict(summary='Fixture contribution', scientific_result='Retain this original contribution with its limits.',
        limitations=['Synthetic native transport; no model or scientific experiment'], implementation_selection=None)
    if role == 'W':payload.update(stage_intent='decision', mechanism_findings=None)
    if role == 'H':payload['search_plan'] = None
    if role == 'A':payload.update(search_review=None, candidate_recommendation=None, progress_assessment=None,
        decision_request=(dict(decision='mechanism',
            question='Which retained observation resolves the remaining fixture gap?',
            change_criterion='An independent observation addressing that gap changes the assessment.')
            if action in {'continue_w', 'revise_h', 'extract'} else None), portfolio_review=None)
    if role in {'H', 'A'}:payload.update(next_action=action, route_reason='Review the retained scientific contribution.')
    if artifact:(c.workspace(root, step) / 'artifacts/result.txt').write_text('Exact original A deliverable.\n')
    c.write(record / 'science-output.json', payload)
    meter = dict(input_tokens=7, cached_input_tokens=2, output_tokens=3)
    c.write(record / 'usage.json', meter)
    outcome = dict(returncode=0, stop_reason=None, native_started=False, cleanup='observed_group_cleaned',
        usage=native.usage(root), raw_ref=c.file_ref(record / 'science-output.json'))
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
    if seal:
        inventory = c.accept_current_inventory(root, step)
        c.seal_result_from_inventory(root, step, inventory=inventory)
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
    """Keep exact known scientific originals, not mutable accepted-head indexes."""
    head = accepted_budget.read(root)
    paths = [root / 'settings.json', root / 'settings-identity.json']
    for step in range(1, head['max_local_step'] + 1):
        job_path = root / 'jobs' / f'{step:03d}.json'
        paths.append(job_path)
        origin = execution.origin(root, step)
        paths.extend(origin / name for name in ('result.json', 'result-identity.json'))
        paths.extend(c.workspace(root, step) / name for name in
            ('conclusion.json', 'science-task.json', 'artifacts/result.txt'))
        if job_path.exists():
            for record in execution.records(root, step):
                paths.extend(record / name for name in ('native-intent.json', 'native-outcome.json',
                    'usage.json', 'usage-observation.json', 'science-output.json', 'checkpoint.json'))
        else:
            paths.extend(origin / name for name in ('native-intent.json', 'native-outcome.json',
                'usage.json', 'usage-observation.json', 'science-output.json'))
    return {path: path.read_bytes() for path in paths if path.is_file()}


def ledger_originals(root):
    head = accepted_budget.read(root)
    return {root / 'control-ledger' / f'{seq:06d}.json':
        (root / 'control-ledger' / f'{seq:06d}.json').read_bytes()
        for seq in range(1, head['seq'] + 1)}


def assert_originals(values):
    assert all(path.read_bytes() == value for path, value in values.items())


def test_shrink_keeps_sealed_third_subject_reads_and_allows_only_one_final_a(tmp_path, monkeypatch):
    root = prepare(tmp_path);three(root)
    scientific = execution.origin(root, 3) / 'science-output.json'
    seal = c.read(execution.origin(root, 3) / 'result-identity.json')
    assert len(seal['materials']) == 1
    material = Path(seal['materials'][0]['source'])
    scientific_raw = scientific.read_bytes();raw = material.read_bytes()
    assert raw == b'Exact original A deliverable.\n'
    retained = {**originals(root), **ledger_originals(root), material: raw}
    shrink(root)
    assert budget.effective_max_steps(root) == 2 and budget.historical_step_limit(root) == 5
    usage = native.usage(root)
    assert usage['observed_tokens'] == usage['accounted_tokens'] == 30
    assert usage['blocking_unknown_steps'] == [] and budget.used_steps(root) == 3
    ledger = ledger_originals(root)
    head_path = root / accepted_budget.HEAD;head_raw = head_path.read_bytes()
    def forbidden(*args, **kwargs):pytest.fail('Historical reads cannot sample budgets or dispatch native work')
    with monkeypatch.context() as patch:
        patch.setattr(native, 'execute_native', forbidden);patch.setattr(runtime, 'start', forbidden)
        # Cold historical reads consume existing originals in place. Retired
        # archive/checkpoint packages cannot reconstruct missing science.
        with monkeypatch.context() as read_patch:
            read_patch.setattr(budget, 'snapshot', forbidden)
            read_patch.setattr(budget, 'clock_stamp', forbidden)
            read_patch.setattr(native, 'usage', forbidden)
            def read_science():
                with science.science(root, 3) as result:
                    return result.stream.read()
            assert read_science() == scientific_raw
            assert json.loads(scientific_raw)['scientific_result'] == 'Retain this original contribution with its limits.'
            members = science.materials(root, 3)
            assert members['total'] == 1 and members['next_after'] is None
            member = members['items'][0]
            assert member['index'] == 1 and member['name'] == 'result.txt'
            def read_material():
                result, name = science.artifact(root, 3, member['index'])
                with result:
                    assert name == 'result.txt'
                    return result.stream.read()
            assert read_material() == raw
            for source, held, reader, expected, missing in (
                    (scientific, tmp_path / 'held-third-science.json', read_science, scientific_raw,
                        'Expected an original regular file'),
                    (material, tmp_path / 'held-third-material.txt', read_material, raw,
                        'Sealed scientific original is missing or inaccessible')):
                before = source.stat()
                # External fixture custody, not a product restoration API:
                # retain the exact inode BEFORE removing its original name.
                held.hardlink_to(source)
                assert held.samefile(source)
                source.unlink()
                try:
                    with pytest.raises(ValueError, match=missing):
                        reader()
                    assert ledger_originals(root) == ledger
                    assert head_path.read_bytes() == head_raw
                    assert not source.exists()  # The reader must not repair it.
                finally:
                    source.hardlink_to(held)
                    held.unlink()
                after = source.stat()
                assert (after.st_dev, after.st_ino, after.st_mode, after.st_mtime_ns) == (
                    before.st_dev, before.st_ino, before.st_mode, before.st_mtime_ns)
                assert reader() == expected
                assert ledger_originals(root) == ledger
                assert head_path.read_bytes() == head_raw
            assert_originals(retained)
        view = control.status(root, limit=2, live_feedback=False)
        assert [row['step'] for row in view['nodes']] == [1, 2] and view['next_after'] == 2
        assert [row['step'] for row in control.status(root, after=2, limit=2, live_feedback=False)['nodes']] == [3]
        assert control.status(root, live_feedback=False)['budget']['max_steps'] == 2
        key = tmp_path / 'ui.key';key.write_bytes(b'fixture-only-local-ui-secret-1234567890');key.chmod(0o600)
        ui = InstanceUI(root, secret_file=key);ui.host = '127.0.0.1:18753'
        _, _, headers = ui.get('/view?' + urlencode(dict(token=ui.read_token)), host=ui.host)
        cookie = headers['Set-Cookie']
        assert b'/results/3' in ui.get('/results', host=ui.host, cookie=cookie)[1]
        code, page, _ = ui.get('/results?after=2', host=ui.host, cookie=cookie)
        assert code == 200 and b'/results/3' in page
        assert ui.get('/results/3', host=ui.host, cookie=cookie)[0] == 200
        code, result, _ = ui.get('/nodes/3/science', host=ui.host, cookie=cookie)
        with result:
            assert code == 200 and result.stream.read() == scientific_raw
        code, result, _ = ui.get('/nodes/3/materials/1', host=ui.host, cookie=cookie)
        with result:
            assert code == 200 and result.stream.read() == raw
    assert native.usage(root) == usage and budget.used_steps(root) == 3
    assert ledger_originals(root) == ledger and head_path.read_bytes() == head_raw
    assert_originals(retained)
    launches = []
    def final_only(selected, step):
        assert selected == root and step == 4
        job = c.read(root / 'jobs/004.json')
        assert job['role'] == 'A' and job['final_only'] is True
        launches.append(step)
        finish(root, step, 'A', [], action='finish', job=job, anchor=budget.accept_node(root, job))
    with monkeypatch.context() as patch:
        patch.setattr(native, 'execute_native', final_only);patch.setattr(runtime, 'start', forbidden)
        result = controller.run(root)
        assert result['status'] == 'final_a_completed'
        final_usage = native.usage(root)
        assert controller.run(root)['status'] == 'final_a_completed'
        assert native.usage(root) == final_usage
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
        native_started=False, cleanup='observed_group_cleaned', metering=metadata))
    # Close the real accepted attempt before removing its job. Accounting must
    # retain the reported lower bound and its unknown coverage without replay.
    c.accept_current_inventory(root, 3)
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
    # Execution uncertainty is not inferred from a billing gap. The missing
    # exact job is instead refused at the current scientific acquisition gate.
    with pytest.raises(ValueError, match='Expected an original regular file'):
        c.accept_current_inventory(root, 3)
    assert not (record / 'continuations/001/native-intent.json').exists()
    assert_originals(retained)


def test_removed_historical_jobs_do_not_refund_usage_or_authorize_an_expensive_new_turn(tmp_path, monkeypatch):
    root = prepare(tmp_path, fragments=True)
    three(root)
    # Prepare under the original grant; admission of its native turn remains a
    # separate current boundary. The operator then reduces the future ceilings.
    job = c.build_job(root, 4, 'W', [])
    budget.amend(root, max_tokens=29, max_steps=4,
        authorization_note='Reduce future work without refunding original consumption.',
        authorization_statement='Retain all original costs; no replacement executions.')
    for step in (1, 2, 3):
        (root / 'jobs' / f'{step:03d}.json').unlink()
    retained = originals(root)
    usage = native.usage(root)
    assert usage['observed_tokens'] == usage['accounted_tokens'] == 30
    assert usage['unknown_steps'] == [] and budget.used_steps(root) == 4
    assert budget.effective_max_steps(root) == 4 and budget.historical_step_limit(root) == 5
    def forbidden(*args, **kwargs):pytest.fail('Consumed originals cannot authorize another native submission')
    monkeypatch.setattr(runtime, 'start', forbidden)
    monkeypatch.setattr(native, 'execute_native', forbidden)
    with pytest.raises(budget.NodeBudgetUnavailable) as stopped:
        budget.accept_node(root, job)
    assert stopped.value.reason == 'node_budget_unavailable_tokens'
    assert not (execution.origin(root, 4) / 'native-intent.json').exists()
    assert native.usage(root) == usage
    assert_originals(retained)


def test_previously_accepted_fragment_chain_survives_shrink_but_next_turn_does_not_fit(tmp_path):
    root = prepare(tmp_path, fragments=True)
    job = c.build_job(root, 1, 'W', [])
    first = execution.origin(root, 1);paths = []
    for number in range(3):
        anchor = budget.accept_node(root, job) if number == 0 else budget.accept_continuation(root, job, paths)
        record = first if number == 0 else first / 'continuations' / f'{number:03d}'
        record.mkdir(parents=True, exist_ok=True)
        intent = dict(step=1, task_ref=c.record_ref(root / 'jobs/001.json'), budget_slice=anchor)
        if number:
            prior = paths[-1];proof = prior / 'checkpoint.json'
            intent.update(attempt=number, continuation_kind='fragment', context_mode='new_context',
                native_home=str(root / 'native-homes/001' / f'context-{number:03d}'),
                root_intent_ref=c.record_ref(first / 'native-intent.json'), previous=execution.proof(prior),
                fragment_checkpoint=dict(path=proof.relative_to(root).as_posix(), ))
        accepted_budget.accept_native(root, 1, record, intent)
        meter = dict(input_tokens=7, cached_input_tokens=2, output_tokens=3)
        c.write(record / 'usage.json', meter)
        c.write(record / 'science-output.json', dict(summary='Native fragment fixture',
            scientific_result='Retain the checkpoint; no model was called.', limitations=['Synthetic native originals'],
            implementation_selection=None, stage_intent='continue', mechanism_findings=None))
        report = dict(budget.node_report(root, job, intent, meter), usage_observed=True)
        # Trusted synthetic stopped-process originals, never live transport or
        # backend qualification. The real inventory owner checks their bindings.
        binding = intent['task_ref'] if number == 0 else c.record_ref(record / 'native-intent.json')
        receipts = {}
        for index, name in enumerate(('runner-process.json', 'native-process.json', 'bridge-process.json'), 1):
            pid = 900000000 + number * 3 + index
            receipt = dict(task_ref=binding, boot_id='synthetic-stopped-boot',
                pid=pid, start_ticks=1, pgid=pid, sid=pid, ppid=1)
            c.write(record / name, receipt)
            receipts[name] = receipt
        c.write(record / 'bridge-outcome.json', dict(process=receipts['bridge-process.json'],
            cleanup='observed_group_cleaned'))
        processes = {name: c.record_ref(record / name)
            for name in ('runner-process.json', 'native-process.json')}
        c.write(record / 'native-outcome.json', dict(returncode=7 if number == 2 else 0, stop_reason=None,
            native_started=True, cleanup='observed_group_cleaned', execution_processes=processes,
            usage=native.usage(root), budget_slice=report,
            raw_ref=c.file_ref(record / 'science-output.json')))
        inventory = c.accept_current_inventory(root, 1)
        assert inventory.record == record and inventory.attempt == number
        assert inventory.native_intent == intent and inventory.native_outcome['budget_slice'] == report
        assert inventory.process_receipts == {name: receipts[name] for name in processes}
        assert inventory.checkpoint['science']['status'] == 'present'
        assert inventory.checkpoint['binding']['attempt'] == number
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


@pytest.mark.process_e2e
def test_later_original_successor_above_initial_ceiling_blocks_resume_after_amendment(tmp_path, monkeypatch):
    from test_worker_resume import instance, fail
    root = instance(tmp_path, steps=5);first = fail(root)
    budget.amend(root, max_steps=8, authorization_note='A finite expanded review window.',
        authorization_statement='Retain prior work and permit bounded follow-up.')
    # An unreconciled successor intent is sufficient to prohibit another native
    # turn for this old stage; it does not assert a scientific result or verdict.
    later = execution.origin(root, 6);later.mkdir(parents=True)
    c.write(later / 'native-intent.json', dict(step=6, task_ref=dict(path='/fixture/jobs/002.json')))
    retained = originals(root)
    retained[later / 'native-intent.json'] = (later / 'native-intent.json').read_bytes()
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
    head_path = root / accepted_budget.HEAD
    previous = c.read(head_path)
    amended = shrink(root)
    retained = {**originals(root), **ledger_originals(root), head_path: head_path.read_bytes()}
    def forbidden(*args, **kwargs):pytest.fail('Historical authorization reads cannot sample time or consumption')
    monkeypatch.setattr(budget, 'snapshot', forbidden)
    monkeypatch.setattr(budget, 'clock_stamp', forbidden)
    monkeypatch.setattr(native, 'usage', forbidden)
    assert budget.effective_max_steps(root) == 2
    assert budget.historical_step_limit(root) == 24
    assert_originals(retained)
    # Validate the pending accepted tail, not an already-folded journal entry.
    c.write(head_path, previous)
    path = root / 'control-ledger' / f'{amended["amendment_seq"]:06d}.json'
    value = c.read(path)
    value['before_max_steps'] = 23;value['max_steps_delta'] = value['after_max_steps'] - 23
    c.write(path, value)
    with pytest.raises(ValueError, match='Budget step amendment predecessor changed'):
        budget.historical_step_limit(root)


@pytest.mark.process_e2e
def test_reconciled_waiting_wake_retains_successor_guard_above_initial_step_ceiling(tmp_path, composed_registry, monkeypatch):
    from test_managed_execution_wait import standard_instance, stop_at_waiting
    from scidiscovery.research_loop import experiments as e, managed_wait, runtime
    root, home = standard_instance(tmp_path, monkeypatch, adaptive_batches=1)
    config, call = stop_at_waiting(root)
    step = config['scope']['step'];assert step == 1
    record = execution.origin(root, step);job = c.read(root / 'jobs' / f'{step:03d}.json')
    work = c.workspace(root, step)
    assert job['role'] == 'W' and job['direct_refs'] == [] and job['research_strategy_version'] == 3
    source = originals(root)
    usage = native.usage(root)
    assert budget.snapshot(root)['clock_phase'] == 'waiting'
    assert budget.used_steps(root) == 1 and len(execution.records(root, step)) == 1
    assert usage['observed_tokens'] == usage['accounted_tokens'] == 12
    consumed = budget.snapshot(root)
    outcome = c.read(record / 'native-outcome.json')
    assert outcome['returncode'] == 0 and outcome['stop_reason'] is None
    assert outcome['raw_ref'] == c.file_ref(record / 'science-output.json')
    assert outcome['native_identity']['session']['source'] == 'native_metadata_hooks'
    assert outcome['native_thread_event']['requested_profile'] == outcome['native_thread_event']['effective_profile']
    assert outcome['native_identity']['session']['home'] == str(home)
    accepted = e.Records(config).accepted(call)
    assert accepted['mechanism_binding']['mechanism_ref'] == 'w001.m1'
    assert accepted['mechanism_binding']['research_binding']['exploration'] is True
    assert accepted['mechanism_binding']['research_binding']['experiment_question']['change_criterion']
    # Terminal notification belongs to this exact accepted call. Pausing here
    # must not seal W, invent a successor or open another native turn.
    (root / 'pause.request').write_text('Retain the original W before its terminal wake.')
    adapter = e.load(config);adapter.complete(call)
    observation_path = e.Records(config).directory(call) / 'execution-observation.json'
    assert not observation_path.exists()
    paused = controller.run(root)  # The real managed poll/reconcile owns acceptance.
    assert paused['status'] == 'waiting_execution' and paused['reason'] == 'paused_before_execution_wake'
    observed = c.read(observation_path)
    assert observed['call_id'] == call and observed['status'] == 'known_success'
    assert (work / 'work/launches').read_text() == 'exec\n'
    assert native.usage(root) == usage
    assert budget.snapshot(root)['consumed_seconds'] >= consumed['consumed_seconds']
    assert not (record / 'result-identity.json').exists()
    assert_originals(source)
    (root / 'pause.request').unlink()
    waiting = managed_wait.validate(root, job, record)
    assert len(waiting['calls']) == 1 and len(e.Records(config).calls()) == 1
    observation = e.proof(e.Records(config).directory(call) / 'execution-observation.json')
    e.verify_observation(c.read(Path(observation['path'])),
        lambda item: Path(item['path']), interface=config['execution_interface'])
    assert c.settings(root)['max_steps'] == 7
    budget.amend(root, max_steps=10, authorization_note='Finite follow-up scope with original waiting result retained.',
        authorization_statement='Preserve the original calls and complete bounded follow-up.')
    assert budget.effective_max_steps(root) == budget.historical_step_limit(root) == 10
    assert native.usage(root) == usage and budget.used_steps(root) == 1
    assert_originals(source)
    # An unreconciled higher successor remains a blocker; no scientific result
    # or fresh execution permission is inferred from this retained intent.
    later = execution.origin(root, 8);later.mkdir(parents=True)
    c.write(later / 'native-intent.json', dict(step=8, task_ref=dict(path='/fixture/jobs/002.json')))
    retained = originals(root)
    retained[later / 'native-intent.json'] = (later / 'native-intent.json').read_bytes()
    ledger = ledger_originals(root)
    def forbidden(*args, **kwargs):pytest.fail('A retained successor must prevent a fresh execution-wake native turn')
    monkeypatch.setattr(runtime, 'start', forbidden)
    with pytest.raises(continuation.ResumeUnavailable, match='worker_resume_successor_present'):
        continuation.wake_start(root, step, [observation])
    assert (work / 'work/launches').read_text() == 'exec\n'
    assert not (record / 'continuations/001').exists()
    assert len(execution.records(root, step)) == len(e.Records(config).calls()) == 1
    assert e.Records(config).accepted(call) == accepted
    assert native.usage(root) == usage
    assert budget.effective_max_steps(root) == 10
    assert budget.snapshot(root)['consumed_seconds'] >= consumed['consumed_seconds']
    assert c.read(Path(observation['path']))['status'] == 'known_success'
    assert all(path.read_bytes() == value for path, value in ledger.items())
    assert_originals(retained)
