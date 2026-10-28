"""FINAL owns one A and the host's original submission, with no science loop."""
import copy
import json

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, delivery, execution, native
from test_history_handoff import finish
from test_soft_external_closeout import external_scene, expire_original_clock


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
    c.write(record/'native-intent.json', dict(step=3, task_ref=c.record_ref(root/'jobs/003.json')))
    meter = dict(input_tokens=100500, cached_input_tokens=100000, output_tokens=5)
    observation = dict(coverage='unknown', reason='terminal_usage_unreported', tokens=meter)
    c.write(record/'usage.json', meter)
    c.write(record/'usage-observation.json', observation)
    c.write(record/'native-outcome.json', dict(returncode=-15, stop_reason=stop_reason,
        cleanup=cleanup, metering=observation, usage=native.usage(root)))
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
    assert c.read(execution.record(root, 4)/'science-result.schema.json')['properties']['next_action']['enum'] == ['deliver']
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
    from test_candidate_audit import _seal_existing
    root = external_scene(tmp_path, monkeypatch)
    cleaned_unknown_author(root)
    nodes = [dict(step=step, seal_ref=c.record_ref(execution.origin(root, step)/'result-identity.json')) for step in (1, 2)]
    refs = delivery.catalogue_sources(root, nodes)
    budget.begin_closeout(root, 2, 4, 'Ordinary model closeout', delivery_sources=refs)
    job = c.build_job(root, 4, 'A', [1, 2], purpose='closeout', source_cutoff=2, delivery_sources=refs)
    payload = dict(summary='Choose the exact retained candidate', scientific_result='Original public evidence only.',
        limitations=['No scientific qualification is asserted.'], implementation_selection=None,
        search_review=None, progress_assessment=None, candidate_recommendation='run-1',
        next_action='deliver', route_reason='Explicit original choice within the retained model boundary')
    _seal_existing(root, 4, payload)
    binding = delivery.prepare_binding(root, job, c.validate(c.workspace(root, 4)))
    assert not job.get('final_only') and binding['decision']['step'] == 4 and binding['candidate_id'] == 'run-1'
    assert budget.final_a_barrier(root) is None and not budget.execution_blocked(root)
    usage=native.usage(root)
    assert usage['accounted_tokens']==usage['observed_tokens']>=budget.snapshot(root)['max_tokens']
    ledger = {path:path.read_bytes() for path in (root/'control-ledger').glob('*.json')}
    leaves = {path:path.read_bytes() for path in delivery.leaf_output(root).glob('*.json')}
    originals = {path:path.read_bytes() for path in (root/'jobs/004.json', execution.raw(root, 4),
        execution.origin(root, 4)/'result-identity.json', execution.origin(root, 3)/'native-outcome.json')}
    with pytest.raises(budget.NodeBudgetUnavailable, match='closeout_budget_unavailable'):
        budget.accept_action(root, 'submit', binding['operation_id'], binding['decision'], binding, 20)
    assert budget.closeout_state(root)['actions'] == []
    assert {path:path.read_bytes() for path in (root/'control-ledger').glob('*.json')} == ledger
    assert {path:path.read_bytes() for path in delivery.leaf_output(root).glob('*.json')} == leaves
    assert all(path.read_bytes() == raw for path,raw in originals.items())
    assert not (root/'host-delivery/action-processes').exists() and not (root/'submission/model.pkl').exists()
    assert native.usage(root)['unknown_steps'] == [3] and native.usage(root)['blocking_unknown_steps'] == []


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
    assert schema['properties']['next_action']['enum'] == ['deliver']
    assert schema['properties']['candidate_recommendation']['type'] == 'string'
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
    from test_final_a_completion import instance
    prepare = controller.prepare
    def without_node_policy(*args, **kwargs):
        kwargs['node_budget_policy'] = None
        kwargs['resume_policy'] = None
        return prepare(*args, **kwargs)
    monkeypatch.setattr(controller, 'prepare', without_node_policy)
    root = instance(tmp_path, max_tokens=shared_ceiling, max_steps=6, mode='soft', route='analyze')
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
    root = external_scene(tmp_path, monkeypatch, max_steps=3)
    finish(root, 3, 'H', [], action='wait')
    expire_original_clock(root, monkeypatch)
    assert controller.closeout(root)['status'] == 'delivery_confirmed'
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
    actions = budget.closeout_state(root)['actions']
    assert all(row['intent']['max_seconds'] == policy['confirm_seconds'] for row in actions[1:])
    assert all(row['intent']['binding'] == actions[0]['intent']['binding'] for row in actions)
    final_job(root)

    # Replay and action identity validation read permission declarations only.
    from test_authorization_boundaries import _forbid_results
    events, _ = budget._events(root, c.settings(root))
    original = actions[-1]['intent']
    previous = events[:original['seq'] - 1]
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        assert len(budget.closeout_state(root)['actions']) == 3
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


def test_final_without_accepted_worker_still_runs_one_a_with_explicit_unsubmitted_result(tmp_path):
    from test_final_a_completion import instance
    root = instance(tmp_path, max_tokens=5, max_steps=6, mode='soft', route='analyze')
    result = controller.run(root)
    assert result['status'] == 'final_a_completed', json.dumps(result, indent=2)
    assert result['reason'] == 'accepted_worker_evidence_unavailable'
    assert result['submission_result']['status'] == 'unsubmitted'
    assert budget.closeout_state(root)['begin']['mode'] == 'final'
    job, state = final_job(root)
    assert job['step'] == 2 and state['actions'] == []


def test_default_optional_node_policy_uses_original_global_a_call_safety(tmp_path, monkeypatch):
    from test_final_a_completion import instance
    prepare = controller.prepare
    def default_prepare(*args, **kwargs):
        kwargs['node_budget_policy'] = None
        kwargs['resume_policy'] = None
        return prepare(*args, **kwargs)
    monkeypatch.setattr(controller, 'prepare', default_prepare)
    root = instance(tmp_path, max_tokens=5, max_steps=6, mode='soft', route='analyze')
    assert controller.run(root)['status'] == 'final_a_completed'
    job, _ = final_job(root)
    assert budget.node_limits(root, job) == dict(max_observed_tokens=5, max_seconds=90)
    assert c.read(execution.record(root, job['step'])/'usage.json')['input_tokens'] == 280900


def normal_fixture(monkeypatch, *, action='wait'):
    execute = native.execute_native
    def execute_fixture(root, step, **kwargs):
        job = c.read(root/'jobs'/f'{step:03d}.json')
        if job.get('final_only'):
            return execute(root, step, **kwargs)
        finish(root, step, job['role'], [int(ref['path'].split('/')[2]) for ref in job['direct_refs']], action=action)
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
    normal_fixture(monkeypatch)
    result = controller.run(root)
    assert result['status'] == 'wait', json.dumps(result, indent=2)
    assert final_path.read_bytes() == original
    assert not (execution.record(root, 3)/'native-intent.json').exists()
    assert budget.closeout_state(root)['begin'] is None
    assert budget.final_a_barrier(root) is None
    resumed = c.read(root/'jobs/004.json')
    assert resumed['role'] == 'A' and not resumed.get('final_only')
    assert [row['step'] for row in resumed['history']['nodes']] == [1, 2]
    assert resumed['normal_resume']['seq'] == budget.normal_resume(root)['seq']
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
    normal_fixture(monkeypatch, action='continue_w')
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
    assert [row['step'] for row in c.read(root/'jobs/005.json')['history']['nodes']] == [1, 2, 4]
    actions = budget.closeout_state(root)['actions']
    assert [row['intent']['action_kind'] for row in actions] == ['submit','confirm','submit','confirm']
    assert actions[0]['intent']['operation_id'] == first_operation
    assert actions[2]['intent']['operation_id'] != first_operation
    # FINAL 5 observed the first public submission before replacing it. Its
    # small observation originals remain readable after the second promotion.
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
    assert controller.run(root)['status'] == 'wait'
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


def test_formal_review_after_resume_keeps_exact_subject_history_and_resume_binding(tmp_path, monkeypatch):
    from scidiscovery.research_loop import formal_review as review
    from test_formal_review import prepare
    root = prepare(tmp_path, ['codex'])
    finish(root,1,'H',[],action='experiment')
    finish(root,2,'W',[1],files={'model.txt':'exact subject; no scientific qualification'})
    requester,_,_ = finish(root,3,'A',[1,2],action='formal_review',selection=['W002-I001'])
    monkeypatch.setattr(review,'authors',lambda *args:dict(complete=True,participants=[['fixture','author']]))
    route = review.request_route(root,requester,c.validate(c.workspace(root,3)),[1,2,3])
    assert route[3]['kind'] == 'formal_review'
    refs = [dict(step=number,seal_ref=c.record_ref(execution.record(root,number)/'result-identity.json')) for number in (1,2,3)]
    cursor = dict(role='A',parents=route[1],instruction=route[2],evidence_return=route[3],
        correction_request=None,author_correction=None,source_refs=refs)
    begin = budget.begin_closeout(root,3,4,'known shared admission boundary',final_only=True,resume_cursor=cursor)
    budget.amend(root,max_tokens=3000,authorization_note='Explicit increase restores the pending exact review route.')
    assert controller._resume_final(root,begin)
    resumed = budget.normal_resume(root)
    assert resumed['first_step'] == 5
    job = c.build_job(root,5,'A',route[1],instruction=route[2],evidence_return=route[3])
    assert job['purpose'] == 'formal_review'
    assert [row['step'] for row in job['history']['nodes']] == [2]
    assert budget.scientific_steps(root,5) == [1,2,3]
    c._validate_inputs(root,c.workspace(root,5),job)
    changed = dict(job,normal_resume={**job['normal_resume'],'path':'host-recovery/unapproved.json'})
    with pytest.raises(ValueError,match='normal resume boundary'):
        c._validate_inputs(root,c.workspace(root,5),changed)
