"""FINAL lifecycle through deterministic native/adapter transport; no real model.

The shared fixture launches local Python and adapter CLI processes, so this
module conservatively groups its related boundary assertions as process_e2e.
"""
import copy
import json

import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, delivery, execution, native, runtime
from test_history_handoff import finish
import test_soft_external_closeout as external_fixture

pytestmark = pytest.mark.process_e2e


def external_scene(tmp_path, monkeypatch, *, accounted_worker=None, **options):
    if accounted_worker is not None:
        original_finish = external_fixture.protocol.finish
        def retained_worker(root, step, role, parents, **kwargs):
            if role != 'W':
                return original_finish(root, step, role, parents, **kwargs)
            work = c.workspace(root, step)
            record = execution.origin(root, step)
            for name, contents in kwargs.get('files', {}).items():
                path = work/'artifacts'/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents)
            intent = dict(task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'))
            c.write(record/'native-intent.json', intent)
            accepted_budget.accept_native(root, step, record, intent)
            c.write(record/'science-output.json', dict(summary='Retained W conclusion',
                scientific_result='Known original transport facts only',
                limitations=['Fixture originals; no scientific qualification'],
                implementation_selection=None, mechanism_findings=None,
                stage_intent='decision', consultation_question=None))
            c.write(record/'usage.json', dict(input_tokens=accounted_worker,
                cached_input_tokens=2, output_tokens=0))
            c.write(record/'native-outcome.json', dict(returncode=0, native_started=False,
                stop_reason=None, usage=native.usage(root),
                raw_ref=c.file_ref(record/'science-output.json')))
            c.seal_result(root, step)
        monkeypatch.setattr(external_fixture.protocol, 'finish', retained_worker)
    return external_fixture.external_scene(tmp_path, monkeypatch, **options)


def report_instance(tmp_path, monkeypatch, **options):
    from scidiscovery.research_loop import backends
    from test_final_a_completion import instance
    from test_final_a_completion_contract import complete
    root = instance(tmp_path, **options)
    def return_report(actual_root, step, attempt=0):
        assert actual_root == root and attempt == 0
        job = c.read(root/'jobs'/f'{step:03d}.json')
        record = execution.origin(root, step)
        _, outcome = complete(root, job, c.read(record/'native-intent.json'))
        backend = backends.selected(c.settings(root, job))
        outcome['native_backend'] = backend.name
        outcome['metering']['source'] = backend.terminal_source
        c.write(record/'usage-observation.json', outcome['metering'])
        c.write(record/'native-outcome.json', outcome)
    monkeypatch.setattr(runtime, 'start', return_report)
    return root


def final_job(root):
    state = budget.closeout_state(root)
    assert state['begin']['mode'] == 'final'
    event = budget.final_a_barrier(root)
    assert event['basis'] == 'final_state'
    job = c.read(root/'jobs'/f'{event["step"]:03d}.json')
    assert job['role'] == 'A' and job['final_only']
    assert not (root/'jobs'/f'{event["step"]+1:03d}.json').exists()
    return job, state


def cleaned_unknown_author(root, *, stop_reason='token_limit', cleanup='observed_group_cleaned'):
    """Only the interrupted author's billing is unknown; W originals stay sealed."""
    c.write(root/'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', 'Fixture original author started')
    c.build_job(root, 3, 'A', [1, 2])
    record = execution.origin(root, 3)
    intent = dict(step=3, task_ref=c.record_ref(root/'jobs/003.json'))
    c.write(record/'native-intent.json', intent)
    accepted_budget.accept_native(root, 3, record, intent)
    meter = dict(input_tokens=100500, cached_input_tokens=100000, output_tokens=5)
    observation = dict(coverage='unknown', reason='terminal_usage_unreported', tokens=meter)
    c.write(record/'usage.json', meter)
    c.write(record/'usage-observation.json', observation)
    c.write(record/'native-outcome.json', dict(returncode=-15, stop_reason=stop_reason,
        cleanup=cleanup, metering=observation, usage=native.usage(root)))
    if cleanup == 'observed_group_cleaned' and stop_reason == 'token_limit':
        accepted_budget.accept_terminal(root, 3, record, meter=meter,
            unknown=True, metering=observation)
    return record


@pytest.mark.parametrize('entry', ['budget_boundary', 'explicit_closeout'])
def test_final_delivers_sealed_worker_despite_cleaned_author_unknown_billing(tmp_path, monkeypatch, entry):
    root = external_scene(tmp_path, monkeypatch)
    record = cleaned_unknown_author(root)
    original = {path: path.read_bytes() for path in record.iterdir() if path.is_file()}
    before = native.usage(root)
    assert before['unknown_steps'] == [3] and before['blocking_unknown_steps'] == []
    result = (controller.closeout(root) if entry == 'explicit_closeout' else
        controller._start_budget_closeout(root, 2, 3, 'A', 'token_limit'))
    assert result['status'] == 'delivery_confirmed', json.dumps(result, indent=2)
    job, state = final_job(root)
    assert job['step'] == 4 and job['delivery_sources']['items']
    assert c.read(execution.record(root, 4)/'science-result.schema.json')['properties']['next_action']['enum'] == ['deliver', 'finish']
    assert 'do not authorize external delivery' not in job['instruction']
    assert [row['intent']['action_kind'] for row in state['actions']] == ['submit', 'confirm']
    assert result['usage']['unknown_steps'] == [3] and result['usage']['blocking_unknown_steps'] == []
    assert result['usage']['observed_tokens'] == before['observed_tokens'] + 280934
    assert result['usage']['accounted_tokens'] == result['usage']['observed_tokens']
    marker = budget.final_a_barrier(root)
    assert (marker['selection']['stopped_original'] is None) == (entry == 'explicit_closeout')
    assert not budget.final_a_usage_unknown(marker, result['usage'])
    own_unknown = dict(result['usage'], unknown_steps=[3, 4], blocking_unknown_steps=[3, 4])
    assert not budget.final_a_usage_unknown(marker, own_unknown)
    assert not budget.final_a_usage_unknown(None, result['usage'])
    assert all(path.read_bytes() == raw for path, raw in original.items())
    assert controller.run(root)['status'] == 'delivery_confirmed'
    assert len(budget.closeout_state(root)['actions']) == 2
    assert all(path.read_bytes() == raw for path, raw in original.items())


def test_unknown_billing_does_not_clear_unresolved_native_execution(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch)
    cleaned_unknown_author(root, stop_reason='cleanup_unknown', cleanup='cleanup_unknown')
    assert budget.execution_blocked(root)
    result = controller._start_budget_closeout(root, 2, 3, 'A', 'token_limit')
    assert result['status'] == 'exported_unsubmitted'
    assert result['reason'] == 'original_execution_unresolved'
    assert budget.final_a_barrier(root) is None
    assert budget.closeout_state(root)['actions'] == []
    assert native.usage(root)['unknown_steps'] == [3] and native.usage(root)['blocking_unknown_steps'] == []


def test_plain_host_action_still_rejects_actual_shared_token_exhaustion(tmp_path, monkeypatch):
    from test_history_handoff import selection_explanation
    root = external_scene(tmp_path, monkeypatch, accounted_worker=100500)
    nodes = [dict(step=step, seal_ref=c.record_ref(execution.origin(root, step)/'result-identity.json')) for step in (1, 2)]
    refs = delivery.catalogue_sources(root, nodes)
    budget.begin_closeout(root, 2, 3, 'Ordinary model closeout', delivery_sources=refs)
    job = c.build_job(root, 3, 'A', [1, 2], purpose='closeout', source_cutoff=2, delivery_sources=refs)
    payload = dict(summary='Choose the exact retained candidate', scientific_result='Original public evidence only.',
        limitations=['No scientific qualification is asserted.'], implementation_selection=None,
        search_review=None, progress_assessment=None, candidate_recommendation='run-1',
        decision_request=None, portfolio_review=None, selection_explanation=selection_explanation(),
        next_action='deliver', route_reason='Explicit original choice within the retained model boundary')
    record = execution.origin(root, 3)
    intent = dict(task_ref=c.record_ref(root/'jobs/003.json'))
    c.write(record/'native-intent.json', intent)
    accepted_budget.accept_native(root, 3, record, intent)
    c.write(record/'science-output.json', payload)
    c.write(record/'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    c.write(record/'native-outcome.json', dict(returncode=0, stop_reason=None, native_started=False,
        cleanup='observed_group_cleaned',
        usage=native.usage(root), raw_ref=c.file_ref(record/'science-output.json')))
    c.seal_result(root, 3)
    binding = delivery.prepare_binding(root, job, c.validate(c.workspace(root, 3)))
    assert not job.get('final_only') and binding['decision']['step'] == 3 and binding['candidate_id'] == 'run-1'
    assert budget.final_a_barrier(root) is None and not budget.execution_blocked(root)
    usage=native.usage(root)
    assert usage['accounted_tokens']==usage['observed_tokens']>=budget.snapshot(root)['max_tokens']
    ledger = {path:path.read_bytes() for path in (root/'control-ledger').glob('*.json')}
    leaves = {path:path.read_bytes() for path in delivery.leaf_output(root).glob('*.json')}
    originals = {path:path.read_bytes() for path in (root/'jobs/003.json', execution.raw(root, 3),
        execution.origin(root, 3)/'result-identity.json', execution.origin(root, 2)/'native-outcome.json')}
    with pytest.raises(budget.NodeBudgetUnavailable, match='closeout_budget_unavailable'):
        budget.accept_action(root, 'submit', binding['operation_id'], binding['decision'], binding, 20)
    assert budget.closeout_state(root)['actions'] == []
    assert {path:path.read_bytes() for path in (root/'control-ledger').glob('*.json')} == ledger
    assert {path:path.read_bytes() for path in delivery.leaf_output(root).glob('*.json')} == leaves
    assert all(path.read_bytes() == raw for path,raw in originals.items())
    assert not (root/'host-delivery/action-processes').exists() and not (root/'submission/model.pkl').exists()
    assert native.usage(root)['unknown_steps'] == [] and native.usage(root)['blocking_unknown_steps'] == []


def test_final_unknown_billing_does_not_allow_repeated_unresolved_submission(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch)
    cleaned_unknown_author(root)
    calls = []
    def unknown(root, intent, remaining, **kwargs):
        calls.append(intent)
        return delivery._result(intent['binding'], 'unknown', 'Original external submission remains unknown')
    monkeypatch.setattr(delivery, 'invoke', unknown)
    assert controller._start_budget_closeout(root, 2, 3, 'A', 'token_limit')['status'] == 'delivery_unknown'
    original = calls[0]
    assert budget.execution_blocked(root)
    with pytest.raises(RuntimeError, match='original unresolved execution'):
        budget.accept_action(root, 'submit', original['operation_id'], original['decision'], original['binding'], 20)
    assert len(calls) == len(budget.closeout_state(root)['actions']) == 1
    assert native.usage(root)['unknown_steps'] == [3] and native.usage(root)['blocking_unknown_steps'] == []


@pytest.mark.parametrize('worker_tokens', [99500, 99990, 100500])
@pytest.mark.parametrize('mode', ['soft'])
def test_one_final_a_submits_at_positive_zero_or_negative_balance(tmp_path, monkeypatch, worker_tokens, mode):
    root = external_scene(tmp_path, monkeypatch, mode=mode, accounted_worker=worker_tokens)
    settings = (root/'settings.json').read_bytes()
    result = controller.closeout(root)
    assert result['status'] == 'delivery_confirmed', json.dumps(result, indent=2)
    job, state = final_job(root)
    schema = c.read(execution.record(root, job['step'])/'science-result.schema.json')
    assert schema['properties']['next_action']['enum'] == ['deliver', 'finish']
    assert [row['intent']['action_kind'] for row in state['actions']] == ['submit', 'confirm']
    assert result['last_confirmed'] == state['actions'][-1]['outcome']['result']
    assert result['usage']['observed_tokens'] > 280934
    assert (root/'settings.json').read_bytes() == settings
    before = {path: path.read_bytes() for path in (root/'control-ledger').glob('*.json') if c.read(path)['kind'] in {'final_a_return','action_intent','action_outcome'}}
    assert controller.run(root)['status'] == 'delivery_confirmed'
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert len(budget.closeout_state(root)['actions']) == 2


@pytest.mark.parametrize('shared_ceiling', [10, 5])
def test_final_does_not_require_optional_node_budget_policy(tmp_path, monkeypatch, shared_ceiling):
    prepare = controller.prepare
    def without_node_policy(*args, **kwargs):
        kwargs['node_budget_policy'] = None
        kwargs['resume_policy'] = None
        return prepare(*args, **kwargs)
    monkeypatch.setattr(controller, 'prepare', without_node_policy)
    root = report_instance(tmp_path, monkeypatch, max_tokens=shared_ceiling, max_steps=6, mode='soft', route='analyze')
    assert c.settings(root).get('node_budget_policy') is None
    assert budget.snapshot(root)['max_tokens'] - native.usage(root)['observed_tokens'] <= 0
    result = controller.run(root)
    assert result['status'] == 'final_a_completed', json.dumps(result, indent=2)
    job, _ = final_job(root)
    assert budget.node_limits(root, job) == dict(max_observed_tokens=shared_ceiling, max_seconds=90)
    assert result['submission_result']['status'] == 'unsubmitted'


@pytest.mark.parametrize('crash', ['before_claim', 'after_claim'])
def test_final_phase_and_exact_claim_survive_restart_without_budget_reclassification(tmp_path, monkeypatch, crash):
    root = external_scene(tmp_path, monkeypatch, accounted_worker=100500)
    original_accept = budget.accept_node
    def interrupted(root, job, **kwargs):
        if crash == 'after_claim':
            original_accept(root, job, **kwargs)
        raise RuntimeError('Deterministic host crash before native intent')
    with monkeypatch.context() as patch:
        patch.setattr(budget, 'accept_node', interrupted)
        assert controller.closeout(root)['status'] == 'closeout_native_unsealed'
    begin = budget.closeout_state(root)['begin']
    assert begin['mode'] == 'final'
    job_path = root/'jobs'/f'{begin["first_step"]:03d}.json'
    original_job = job_path.read_bytes()
    if crash == 'after_claim':
        original_claim = budget.final_a_barrier(root)
    budget.amend(root, max_tokens=2_000_000, max_seconds=2000, authorization_note='Transport fixture retains the persisted FINAL phase after adding balance.')
    assert controller.run(root)['status'] == 'delivery_confirmed'
    assert job_path.read_bytes() == original_job
    if crash == 'after_claim':
        assert budget.final_a_barrier(root) == original_claim
    _, state = final_job(root)
    assert len([c.read(path) for path in (root/'control-ledger').glob('*.json') if c.read(path)['kind'] == 'final_a_return']) == 1
    assert len(state['actions']) == 2


def test_budget_denied_prepared_ordinary_a_is_preserved_then_explicit_final_runs(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch, accounted_worker=100500)
    c.build_job(root, 3, 'A', [1, 2])
    path = root/'jobs/003.json';original = path.read_bytes()
    assert controller.run(root)['status'] == 'delivery_confirmed'
    job, _ = final_job(root)
    assert job['step'] == 4
    assert path.read_bytes() == original
    assert not (execution.record(root, 3)/'native-intent.json').exists()
    assert budget.closeout_state(root)['begin']['stopped_original']['step'] == 3
    assert all('/003/' not in ref['path'] for ref in job['direct_refs'])


def test_final_overruns_shared_time_and_steps_but_retains_original_receipts(tmp_path, monkeypatch):
    from test_history_handoff import selection_explanation
    root = external_scene(tmp_path, monkeypatch, max_steps=3)
    finish(root, 3, 'H', [], action='wait')
    stamp = budget.clock_stamp()
    if not (root/'host-started.json').exists():
        c.write(root/'host-started.json', stamp)
    budget.transition(root, 'advance', 'Fixture begins its measured active interval')
    stamp = dict(stamp, at_unix=stamp['at_unix']+1000, at_boottime=stamp['at_boottime']+1000)
    monkeypatch.setattr(budget, 'clock_stamp', lambda: stamp)
    budget.transition(root, 'paused', 'Fixture retains one measured exhausted interval')
    assert budget.snapshot(root)['seconds_remaining'] == 0
    def return_delivery(root, step):
        job = c.read(root/'jobs'/f'{step:03d}.json')
        assert step == 4 and job['final_only']
        record = execution.origin(root, step)
        assert not (record/'native-intent.json').exists()
        intent = dict(step=step, task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'),
            budget_slice=budget.accept_node(root, job))
        c.write(record/'native-intent.json', intent)
        accepted_budget.accept_native(root, step, record, intent)
        meter = dict(input_tokens=280900, cached_input_tokens=100000, output_tokens=34)
        observation = dict(coverage='terminal', source='turn.completed', tokens=meter)
        c.write(record/'usage.json', meter)
        c.write(record/'usage-observation.json', observation)
        c.write(record/'science-output.json', dict(summary='Retained delivery choice',
            scientific_result='Synthetic original receipt; no model or experiment was run.',
            limitations=['Scientific qualification is not asserted.'], implementation_selection=None,
            search_review=None, progress_assessment=None, decision_request=None, portfolio_review=None,
            next_action='deliver', candidate_recommendation='run-1',
            selection_explanation=selection_explanation(), route_reason='Exact retained fixture candidate'))
        usage = native.usage(root)
        c.write(record/'native-outcome.json', dict(returncode=0, native_started=False,
            stop_reason=None, cleanup='observed_group_cleaned', metering=observation, usage=usage,
            raw_ref=c.file_ref(record/'science-output.json'), final_a_completion=budget.final_a_receipt(
                budget.final_a_return(root, job, intent), intent, meter, usage)))
        c.seal_result(root, step)
    monkeypatch.setattr(native, 'execute_native', return_delivery)
    result = controller.closeout(root)
    assert result['status'] == 'delivery_confirmed', {
        key: result.get(key) for key in ('status', 'reason', 'attempt_step', 'submission_result')}
    job, state = final_job(root)
    assert job['step'] == 4 and budget.effective_max_steps(root) == 3
    assert budget.used_steps(root) == 6
    assert budget.snapshot(root)['consumed_seconds'] > budget.snapshot(root)['max_seconds']
    assert state['actions'][-1]['outcome']['result']['status'] == 'confirmed'


def test_final_unknown_submission_confirms_same_original_once_per_explicit_run(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch, accounted_worker=100500)
    calls = []
    def unknown(root, intent, remaining, **kwargs):
        calls.append((intent['action_kind'], intent['operation_id']))
        return delivery._result(intent['binding'], 'unknown', 'Exact original operation is unverified')
    monkeypatch.setattr(delivery, 'invoke', unknown)
    assert controller.closeout(root)['status'] == 'delivery_unknown'
    job, state = final_job(root)
    original_job = (root/'jobs'/f'{job["step"]:03d}.json').read_bytes()
    claim = budget.final_a_barrier(root)
    first_used = budget.used_steps(root)
    first_usage = native.usage(root)
    first_time = budget.snapshot(root)['consumed_seconds']
    assert controller.run(root)['status'] == 'delivery_unknown'
    assert budget.used_steps(root) == first_used + 1
    assert controller.run(root)['status'] == 'delivery_unknown'
    assert budget.used_steps(root) == first_used + 2
    assert [kind for kind, _ in calls] == ['submit', 'confirm', 'confirm']
    assert len({operation for _, operation in calls}) == 1
    assert budget.final_a_barrier(root) == claim
    assert native.usage(root) == first_usage
    assert budget.snapshot(root)['consumed_seconds'] >= first_time
    assert (root/'jobs'/f'{job["step"]:03d}.json').read_bytes() == original_job
    policy = c.settings(root)['delivery_policy']
    events, _ = budget.audit_events(root)
    actions = [event for event in events if event['kind'] == 'action_intent']
    assert len(actions) == 3
    assert all(row['max_seconds'] == policy['confirm_seconds'] for row in actions[1:])
    assert all(row['binding'] == actions[0]['binding'] for row in actions)
    final_job(root)

    # Replay and action identity validation read permission declarations only.
    from test_authorization_boundaries import _forbid_results
    original = actions[-1]
    previous = events[:original['seq'] - 1]
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        assert [row['intent']['seq'] for row in budget.closeout_state(root)['actions']] == [
            actions[0]['seq'], actions[-1]['seq']]
        budget._validate_final_a_action(root, original, previous)
        for change in ('submit', 'operation', 'decision', 'candidate', 'claim', 'window'):
            changed = copy.deepcopy(original)
            if change == 'submit':
                changed.update(action_kind='submit', max_seconds=policy['submit_seconds'])
            elif change == 'operation':
                changed['operation_id'] = 'another-operation'
                changed['binding']['operation_id'] = changed['operation_id']
            elif change == 'decision':
                changed['decision']['raw_ref'] = '0' * 64
                changed['binding']['decision'] = changed['decision']
            elif change == 'candidate':
                changed['binding']['source']['candidate_id'] = 'another-candidate'
            elif change == 'claim':
                changed['final_return']['record'] = dict(path='/unapproved/control-ledger/000001.json')
            else:
                changed['max_seconds'] += 1
            with pytest.raises(ValueError, match='Final delivery'):
                budget._validate_final_a_action(root, changed, previous)


def test_final_without_accepted_worker_still_runs_one_a_with_explicit_unsubmitted_result(tmp_path, monkeypatch):
    root = report_instance(tmp_path, monkeypatch, max_tokens=5, max_steps=6, mode='soft', route='analyze')
    result = controller.run(root)
    assert result['status'] == 'final_a_completed', json.dumps(result, indent=2)
    assert result['reason'] == 'scientific_delivery_declined'
    assert result['submission_result']['status'] == 'unsubmitted'
    assert budget.closeout_state(root)['begin']['mode'] == 'final'
    job, state = final_job(root)
    assert job['step'] == 2 and state['actions'] == []


def test_default_optional_node_policy_uses_original_global_a_call_safety(tmp_path, monkeypatch):
    prepare = controller.prepare
    def default_prepare(*args, **kwargs):
        kwargs['node_budget_policy'] = None
        kwargs['resume_policy'] = None
        return prepare(*args, **kwargs)
    monkeypatch.setattr(controller, 'prepare', default_prepare)
    root = report_instance(tmp_path, monkeypatch, max_tokens=5, max_steps=6, mode='soft', route='analyze')
    assert controller.run(root)['status'] == 'final_a_completed'
    job, _ = final_job(root)
    assert budget.node_limits(root, job) == dict(max_observed_tokens=5, max_seconds=90)
    assert c.read(execution.record(root, job['step'])/'usage.json')['input_tokens'] == 1200


def normal_fixture(monkeypatch, *, pause=True):
    execute = native.execute_native
    def execute_fixture(root, step, **kwargs):
        job = c.read(root/'jobs'/f'{step:03d}.json')
        if job.get('final_only'):
            return execute(root, step, **kwargs)
        finish(root, step, job['role'], [int(ref['path'].split('/')[2]) for ref in job['direct_refs']], action='continue_w')
        if pause:
            (root/'pause.request').touch()
    monkeypatch.setattr(native, 'execute_native', execute_fixture)


@pytest.mark.parametrize('claim_first', [False, True])
def test_explicit_raise_reopens_unused_final_at_new_id_from_saved_normal_cursor(tmp_path, monkeypatch, claim_first):
    root = external_scene(tmp_path, monkeypatch, accounted_worker=100500)
    execute = native.execute_native
    def interrupted(root, step, **kwargs):
        if claim_first:
            job = c.read(root/'jobs'/f'{step:03d}.json')
            budget.accept_node(root, job)
        raise RuntimeError('Fixture interrupts before final native intent')
    with monkeypatch.context() as patch:
        patch.setattr(native, 'execute_native', interrupted)
        assert controller.run(root)['status'] == 'closeout_native_unsealed'
    phase = budget.closeout_state(root)['begin']
    assert phase['resume_cursor']['role'] == 'A'
    assert [ref['step'] for ref in phase['resume_cursor']['source_refs']] == [1, 2]
    final_path = root/'jobs/003.json';original = final_path.read_bytes()
    assert not (execution.record(root, 3)/'native-intent.json').exists()
    budget.amend(root, max_tokens=500000, authorization_note='Explicitly raise the shared threshold to continue the pending normal task.')
    prepared = copy.deepcopy(dict(accepted_budget.read(root)))
    assert prepared['current_job']['step'] == 3 and prepared['current_job']['status'] == 'prepared'
    assert prepared['current_job']['native_record'] is None and prepared['current_native'] is None
    normal_fixture(monkeypatch)
    result = controller.run(root)
    assert result['status'] == 'paused', json.dumps(result, indent=2)
    assert final_path.read_bytes() == original
    assert not (execution.record(root, 3)/'native-intent.json').exists()
    assert budget.closeout_state(root)['begin'] is None
    assert budget.final_a_barrier(root) is None
    resumed = c.read(root/'jobs/004.json')
    assert resumed['role'] == 'A' and not resumed.get('final_only')
    assert budget.scientific_steps(root, 4) == [1, 2]
    assert resumed['normal_resume']['seq'] == budget.normal_resume(root)['seq']
    event = c.read(root/'control-ledger'/f'{budget.normal_resume(root)["seq"]:06d}.json')
    assert event['head_updates']['current_job'] is None and event['index_updates'] == {}
    originals = {path: path.read_bytes() for path in (
        root/accepted_budget.HEAD, accepted_budget._row_path(root, 'job/003'))}
    unchanged = copy.deepcopy(prepared)
    accepted_budget._validate_budget_transition(prepared, event)
    for field in ('current_native', 'native_record'):
        changed = copy.deepcopy(prepared)
        if field == 'current_native':
            changed[field] = dict(record='native-evidence/003')
        else:
            changed['current_job'][field] = 'native-evidence/003'
        rejected = copy.deepcopy(changed)
        with pytest.raises(ValueError, match='exact unstarted FINAL job'):
            accepted_budget._validate_budget_transition(changed, event)
        assert changed == rejected
    assert prepared == unchanged
    assert all(path.read_bytes() == value for path, value in originals.items())
    assert not (root/'jobs/005.json').exists()
    assert len(budget.closeout_state(root)['actions']) == 0


def test_explicit_insufficient_raise_or_decrease_does_not_reopen_or_repeat_final_a(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch, accounted_worker=100500)
    assert controller.run(root)['status'] == 'delivery_confirmed'
    old_job = (root/'jobs/003.json').read_bytes()
    old_claim = budget.final_a_barrier(root)
    consumed = native.usage(root)['accounted_tokens']
    for ceiling in (100, consumed-1):
        budget.amend(root, max_tokens=ceiling, authorization_note='Fixture amendment remains insufficient for normal admission.')
        assert controller.run(root)['status'] == 'delivery_confirmed'
        assert budget.final_a_barrier(root) == old_claim
        assert (root/'jobs/003.json').read_bytes() == old_job
        assert not (root/'jobs/004.json').exists()
    assert len(budget.closeout_state(root)['actions']) == 2


def test_two_final_cycles_keep_all_usage_claims_and_original_delivery_operations(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch, accounted_worker=100500)
    assert controller.run(root)['status'] == 'delivery_confirmed'
    first_claim = budget.final_a_barrier(root)
    first_paths = [root/'jobs/003.json', execution.record(root,3)/'native-intent.json', execution.record(root,3)/'native-outcome.json', execution.record(root,3)/'result-identity.json']
    originals = {path:path.read_bytes() for path in first_paths}
    first_operation = budget.closeout_state(root)['actions'][0]['intent']['operation_id']
    consumed = native.usage(root)['accounted_tokens']
    budget.amend(root, max_tokens=consumed+5, authorization_note='Explicitly raise the threshold enough for the next soft normal A only.')
    normal_fixture(monkeypatch, pause=False)
    result = controller.run(root)
    assert result['status'] == 'delivery_confirmed', json.dumps(result, indent=2)
    second_claim = budget.final_a_barrier(root)
    assert second_claim['step'] == 5 and second_claim['seq'] != first_claim['seq']
    claims = [c.read(path) for path in sorted((root/'control-ledger').glob('*.json')) if c.read(path)['kind'] == 'final_a_return']
    assert [row['step'] for row in claims] == [3, 5]
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert c.validate(c.workspace(root,3))['next_action'] == 'deliver'
    assert c.read(root/'jobs/004.json')['role'] == 'A'
    assert not c.read(root/'jobs/004.json').get('final_only')
    assert budget.scientific_steps(root, 5) == [1, 2, 4]
    actions = budget.closeout_state(root)['actions']
    assert [row['intent']['action_kind'] for row in actions] == ['submit','confirm']
    assert actions[0]['intent']['operation_id'] != first_operation
    events, _ = budget.audit_events(root)
    intents = [event for event in events if event['kind'] == 'action_intent']
    assert [event['action_kind'] for event in intents] == ['submit','confirm','submit','confirm']
    assert [event['operation_id'] for event in intents] == [first_operation, first_operation,
        actions[0]['intent']['operation_id'], actions[0]['intent']['operation_id']]
    # The second final claim has its own logical operation. Its retained view
    # still binds the first operation's exact public submission original.
    seal=c.read(execution.record(root,5)/'result-identity.json')
    observation=c.read(root/seal['observations']['path'])
    actual=observation['actual_submission']
    receipt=actual['evidence'][0]
    assert receipt['path']=='runs/submitted.json'
    assert receipt['snapshot']!=receipt['path']
    assert c.read(root/receipt['snapshot'])['run_id']==1
    c.validate(c.workspace(root,5))
    assert result['current_actual']['operation_id'] == actions[-1]['intent']['operation_id']
    assert native.usage(root)['accounted_tokens'] > consumed+280934
    assert budget.used_steps(root) == 9
    assert not (root/'jobs/006.json').exists()


def test_raise_after_final_journal_before_job_crash_keeps_reserved_id(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch, accounted_worker=100500)
    build = c.build_job
    def interrupted(root, step, role, parents, **kwargs):
        if role == 'A':
            raise RuntimeError('Fixture crash after FINAL journal, before final job')
        return build(root, step, role, parents, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(c, 'build_job', interrupted)
        with pytest.raises(RuntimeError, match='before final job'):
            controller.run(root)
    begin = budget.closeout_state(root)['begin']
    assert begin['first_step'] == 3 and not (root/'jobs/003.json').exists()
    budget.amend(root, max_tokens=500000, authorization_note='Explicit increase after a pre-job host interruption.')
    normal_fixture(monkeypatch)
    assert controller.run(root)['status'] == 'paused'
    assert not (root/'jobs/003.json').exists()
    assert c.read(root/'jobs/004.json')['role'] == 'A'
    assert not c.read(root/'jobs/004.json').get('final_only')


def test_latest_threshold_decrease_does_not_reopen_even_after_prior_increase(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch, accounted_worker=100500)
    assert controller.run(root)['status'] == 'delivery_confirmed'
    claim = budget.final_a_barrier(root)
    budget.amend(root, max_tokens=1000000, authorization_note='Explicit increase, not yet consumed by run.')
    budget.amend(root, max_tokens=900000, authorization_note='Latest operator decision lowers the threshold; it does not request reopening.')
    assert budget.snapshot(root)['max_tokens']-native.usage(root)['accounted_tokens'] > 100000
    assert controller.run(root)['status'] == 'delivery_confirmed'
    assert budget.final_a_barrier(root) == claim
    assert not (root/'jobs/004.json').exists()


def test_consultation_after_resume_keeps_exact_requester_history_and_resume_binding(tmp_path):
    from test_worker_led_strategy import instance
    root = instance(tmp_path)
    requester,_,_ = finish(root,1,'W',[],stage_intent='review',
        files={'model.txt':'exact retained requester; no scientific qualification'})
    route = controller._route(root,1,'W',c.validate(c.workspace(root,1)),requester,None,None)
    assert route.evidence_return == dict(role='W',step=1)
    refs = [dict(step=1,seal_ref=c.record_ref(execution.record(root,1)/'result-identity.json'))]
    cursor = dict(role='A',parents=list(route.parents),instruction=route.instruction,evidence_return=route.evidence_return,
        correction_request=None,author_correction=None,source_refs=refs)
    begin = budget.begin_closeout(root,1,2,'known shared admission boundary',final_only=True,resume_cursor=cursor)
    budget.amend(root,max_tokens=200000,authorization_note='Explicit increase restores the pending exact consultation route.')
    assert controller._resume_final(root,begin)
    resumed = budget.normal_resume(root)
    assert resumed['first_step'] == 3
    job = c.build_job(root,3,'A',list(route.parents),instruction=route.instruction,evidence_return=route.evidence_return)
    assert job['purpose'] == 'worker_consultation'
    assert job['evidence_return'] == dict(role='W', step=1)
    assert [row['path'] for row in job['direct_refs']] == ['inputs/parents/001/conclusion.json']
    assert budget.scientific_steps(root,3) == [1]
    c._validate_inputs(root,c.workspace(root,3),job)
    original = (root/'jobs/003.json').read_bytes()
    changed = dict(job,normal_resume={**resumed,'path':'host-recovery/unapproved.json'})
    with pytest.raises(ValueError,match='Scientific inputs differ from their accepted canonical job'):
        c._validate_inputs(root,c.workspace(root,3),changed)
    assert (root/'jobs/003.json').read_bytes() == original
    assert not (execution.origin(root,3)/'native-intent.json').exists()
