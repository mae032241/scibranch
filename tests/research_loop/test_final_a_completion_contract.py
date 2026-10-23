"""Final A authorization is exact, nonrenewable, and honest about consumption."""
import copy

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, execution, native
from test_history_handoff import finish


def prepare(tmp_path, *, max_steps=2, legacy=False):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain the bounded findings and their unresolved limitations.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'],
        max_tokens=1000, max_seconds=200, max_steps=max_steps,
        model='fixture-no-model', reasoning_effort='high', node_budget_mode='soft',
        node_budget_policy={role: dict(max_observed_tokens=100, max_seconds=10)
                            for role in 'HWAE'})
    if legacy:
        # Construct a historical frozen configuration before any ledger event.
        settings = c.settings(root)
        settings.pop('final_a_completion_policy')
        c.write(root / 'settings.json', settings)
        c.write(root / 'settings-identity.json', dict(path='settings.json'))
    finish(root, 1, 'H', [], action='analyze')
    return root


def final_job(root):
    if budget.closeout_state(root)['begin'] is None:
        budget.begin_closeout(root, 1, 2, 'Explicit fixture FINAL phase', final_only=True)
    return c.build_job(root, 2, 'A', [1],
        final_report=dict(source_cutoff=1, stopped_original=None))


def accept(root, role='A', *, final=True):
    job = final_job(root) if final else c.build_job(root, 2, role, [1])
    intent = dict(step=2, task_ref=c.record_ref(root / 'jobs/002.json'),
                  budget_slice=budget.accept_node(root, job))
    c.write(execution.origin(root, 2) / 'native-intent.json', intent)
    return job, intent


@pytest.fixture
def original_final(tmp_path):
    root = prepare(tmp_path)
    job, intent = accept(root)
    return root, job, intent


def complete(root, job, intent, *, unknown=False):
    """Publish deterministic native facts; this never launches a Worker."""
    record = execution.origin(root, job['step'])
    meter = dict(input_tokens=1200, cached_input_tokens=1199, output_tokens=10)
    observation = dict(coverage='unknown' if unknown else 'terminal',
        source='turn.completed', tokens=meter)
    c.write(record / 'usage.json', meter)
    c.write(record / 'usage-observation.json', observation)
    payload = dict(summary='Retained bounded findings', scientific_result='Fixture observation',
        limitations=['No experiment or model was run'], implementation_selection=None,
        candidate_recommendation=None, search_review=None, progress_assessment=None,
        decision_request=None, portfolio_review=None, next_action='finish',
        route_reason='The original finite report is complete')
    c.write(record / 'science-output.json', payload)
    accepted = budget.final_a_return(root, job, intent)
    usage = native.usage(root)
    outcome = dict(returncode=-15 if unknown else 0,
        stop_reason='token_limit' if unknown else None, cleanup='observed_group_cleaned',
        metering=observation, usage=usage, raw_ref=c.file_ref(record / 'science-output.json'),
        final_a_completion=budget.final_a_receipt(accepted, intent, meter, usage))
    c.write(record / 'native-outcome.json', outcome)
    return meter, outcome


def test_new_prepare_freezes_one_original_final_a_grant(original_final):
    root, job, intent = original_final
    assert c.settings(root)['final_a_completion_policy'] == dict(version=1)
    accepted = budget.final_a_return(root, job, intent)
    marker = intent['budget_slice']['final_return']
    assert accepted['kind'] == 'final_a_return'
    assert accepted['version'] == 2 and accepted['attempt'] == 0
    assert accepted['step'] == job['step'] == 2
    assert accepted['basis'] == 'final_state'
    assert budget.closeout_state(root)['begin']['mode'] == 'final'
    assert job['final_only']
    assert marker == dict(seq=accepted['seq'], record=c.record_ref(root/'control-ledger'/f'{accepted["seq"]:06d}.json'))
    assert accepted['anchor']['task_ref'] == intent['task_ref']
    assert accepted['anchor']['shared_limits'] == dict(max_tokens=1000, max_seconds=200)
    assert budget.final_a_barrier(root) == accepted
    assert budget.final_a_barrier(root, before_step=2) is None
    assert budget.final_a_barrier(root, before_step=3) == accepted


@pytest.mark.parametrize('change', ['missing', 'seq', 'digest', 'task', 'role', 'step',
                                  'attempt_zero', 'attempt_one', 'continuation', 'local_limit'])
def test_final_grant_cannot_be_moved_or_reused_by_a_continuation(original_final, change):
    root, job, intent = original_final
    changed_job, changed_intent = copy.deepcopy(job), copy.deepcopy(intent)
    if change == 'missing':
        changed_intent['budget_slice'].pop('final_return')
    elif change == 'seq':
        changed_intent['budget_slice']['final_return']['seq'] += 1
    elif change == 'digest':
        changed_intent['budget_slice']['final_return']['record'] = dict(path='/unapproved/control-ledger/000001.json')
    elif change == 'task':
        changed_intent['task_ref'] = '0' * 64
    elif change == 'role':
        changed_job['role'] = 'W'
    elif change == 'step':
        changed_intent['step'] += 1
    elif change.startswith('attempt_'):
        changed_intent['attempt'] = 0 if change == 'attempt_zero' else 1
    elif change == 'continuation':
        changed_intent['continuation_kind'] = 'author_closeout'
    else:
        changed_intent['budget_slice']['limits']['max_observed_tokens'] += 1
    with pytest.raises(ValueError, match='Final A return'):
        budget.final_a_return(root, changed_job, changed_intent)


@pytest.mark.parametrize('change', ['version', 'attempt', 'role', 'used_steps', 'reserve', 'basis'])
def test_final_grant_ledger_replay_rejects_changed_admission(original_final, change):
    root, job, intent = original_final
    accepted = budget.final_a_return(root, job, intent)
    path = root / 'control-ledger' / f'{accepted["seq"]:06d}.json'
    altered = copy.deepcopy(accepted)
    if change in {'version', 'attempt'}:
        altered[change] += 1
    elif change == 'role':
        altered['anchor']['role'] = 'W'
    elif change == 'used_steps':
        altered['selection']['used_steps'] = 0
    elif change == 'reserve':
        altered['reserve']['tokens'] += 1
    else:
        altered['basis'] = 'invented_return'
    c.write(path, altered)
    with pytest.raises(ValueError, match='final A|Final A'):
        budget.final_a_barrier(root)


def test_final_receipt_retains_actual_cached_usage_and_original_ceilings(original_final):
    root, job, intent = original_final
    settings = (root / 'settings.json').read_bytes()
    meter, outcome = complete(root, job, intent)
    usage = native.usage(root)
    assert usage['input_tokens'] == 1207
    assert usage['cached_input_tokens'] == 1201
    assert usage['output_tokens'] == 13
    assert usage['observed_tokens'] == usage['accounted_tokens'] == 1220
    assert usage['unknown_steps'] == []
    assert outcome['final_a_completion']['final_overrun'] == dict(
        original_shared_max_tokens=1000, original_local_target_tokens=100,
        accounted_shared_tokens=1220, observed_node_tokens=1210,
        shared_tokens_over_ceiling=220, local_tokens_over_target=1110)
    assert budget.validate_final_a_receipt(root, job, intent, outcome, meter)['step'] == 2
    assert budget.snapshot(root)['max_tokens'] == 1000
    assert (root / 'settings.json').read_bytes() == settings
    assert budget.node_report(root, job, intent, meter)['observed_tokens'] == 1210
    assert budget.stop_node(root, job, intent, budget.snapshot(root), meter) is None


def test_final_receipt_does_not_convert_unknown_usage_into_terminal_evidence(original_final):
    root, job, intent = original_final
    meter, outcome = complete(root, job, intent, unknown=True)
    assert budget.validate_final_a_receipt(root, job, intent, outcome, meter)['step'] == 2
    assert native.usage(root)['unknown_steps'] == [2]
    # Billing coverage is retained; unresolved execution blocks sealing below.
    assert native.usage(root)['blocking_unknown_steps'] == []
    assert native.usage(root)['accounted_tokens'] == 1220
    with pytest.raises(ValueError, match='Terminal scientific output'):
        c.seal_result(root, 2)
    assert not (execution.origin(root, 2) / 'result-identity.json').exists()


@pytest.mark.parametrize('missing_meter', [False, True])
def test_known_final_execution_seals_with_unknown_billing_without_another_grant(original_final, missing_meter):
    root, job, intent = original_final
    meter, outcome = complete(root, job, intent, unknown=True)
    record = execution.origin(root, 2)
    outcome.update(returncode=0, stop_reason=None)
    if missing_meter:
        (record / 'usage.json').unlink()
        meter = None
    outcome['usage'] = native.usage(root)
    accepted = budget.final_a_return(root, job, intent)
    outcome['final_a_completion'] = budget.final_a_receipt(accepted, intent, meter, outcome['usage'])
    c.write(record / 'native-outcome.json', outcome)
    assert budget.validate_final_a_receipt(root, job, intent, outcome)['step'] == 2
    assert c.seal_result(root, 2)['next_action'] == 'finish'
    assert c.validate(c.workspace(root, 2))['next_action'] == 'finish'
    assert native.usage(root)['unknown_steps'] == [2]
    assert native.usage(root)['blocking_unknown_steps'] == []
    assert outcome['final_a_completion']['final_overrun']['usage_coverage'] == 'unknown'
    if missing_meter:
        assert not (record / 'usage.json').exists()
        assert outcome['final_a_completion']['final_overrun']['observed_node_tokens'] is None
        assert outcome['final_a_completion']['final_overrun']['local_tokens_over_target'] is None
    assert budget.final_a_return(root, job, intent) == accepted


@pytest.mark.parametrize('change', ['receipt_missing', 'overrun', 'cleanup',
                                  'source', 'tokens', 'cached_tokens'])
def test_final_completion_requires_exact_receipt_terminal_meter_and_cleanup(original_final, change):
    root, job, intent = original_final
    meter, outcome = complete(root, job, intent)
    altered = copy.deepcopy(outcome)
    if change == 'receipt_missing':
        altered.pop('final_a_completion')
    elif change == 'overrun':
        altered['final_a_completion']['final_overrun']['shared_tokens_over_ceiling'] = 0
    elif change == 'cleanup':
        altered['cleanup'] = 'unknown'
    elif change == 'source':
        altered['metering']['source'] = 'live_session'
    elif change == 'tokens':
        altered['metering']['tokens']['output_tokens'] += 1
    else:
        meter['cached_input_tokens'] = meter['input_tokens'] + 1
    with pytest.raises(ValueError, match='Final A completion'):
        budget.validate_final_a_receipt(root, job, intent, altered, meter)


@pytest.mark.parametrize('name', ['native-outcome.json', 'usage.json'])
def test_seal_binds_final_receipt_and_usage_bytes(original_final, name):
    root, job, intent = original_final
    complete(root, job, intent)
    c.seal_result(root, 2)
    assert c.validate(c.workspace(root, 2))['next_action'] == 'finish'
    path = execution.origin(root, 2) / name
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='Final A completion receipt identity changed'):
        c.validate(c.workspace(root, 2))


def test_crash_before_intent_reuses_only_the_original_grant(tmp_path):
    root = prepare(tmp_path)
    job = final_job(root)
    first = budget.accept_node(root, job)
    assert not (execution.origin(root, 2) / 'native-intent.json').exists()
    assert budget.accept_node(root, job) == first
    grants = [c.read(path) for path in (root / 'control-ledger').glob('*.json')
              if c.read(path)['kind'] == 'final_a_return']
    assert len(grants) == 1
    assert grants[0]['anchor']['task_ref'] == c.record_ref(root / 'jobs/002.json')


def test_budget_amendment_preserves_the_original_final_claim_and_consumption(original_final):
    root, job, intent = original_final
    complete(root, job, intent)
    original = budget.final_a_return(root, job, intent)
    ledger_path = root / 'control-ledger' / f'{original["seq"]:06d}.json'
    ledger_bytes = ledger_path.read_bytes()
    budget.amend(root, max_tokens=2000, max_seconds=400, max_steps=6,
        authorization_note='Raise the total allowance while retaining this exact final original.',
        authorization_statement='Retain the original final report and all consumption.')
    assert budget.final_a_return(root, job, intent) == original
    assert ledger_path.read_bytes() == ledger_bytes
    grants = [c.read(path) for path in (root / 'control-ledger').glob('*.json')
              if c.read(path)['kind'] == 'final_a_return']
    assert grants == [original]
    assert budget.effective_max_steps(root) == 6
    # This explicit FINAL fixture has no saved normal cursor to reopen.
    for role in 'HWAE':
        assert budget.node_admission(root, dict(step=3, role=role)) == 'final_a_return_consumed'
        with pytest.raises(ValueError, match='no further scientific task'):
            c.build_job(root, 3, role, [])
    assert native.usage(root)['observed_tokens'] == 1220
    assert native.usage(root)['unknown_steps'] == []
    with pytest.raises(budget.NodeBudgetUnavailable, match='final_a_return_consumed'):
        budget.accept_continuation(root, job, [execution.origin(root, 2)])
    assert not (root / 'jobs/003.json').exists()


def test_final_completion_keeps_local_time_finite_despite_shared_time_exhaustion(original_final):
    root, job, intent = original_final
    meter = dict(input_tokens=2000, cached_input_tokens=1999, output_tokens=50)
    limits = budget.snapshot(root)
    limits['seconds_remaining'] = 0
    deadline = intent['budget_slice']['advance_seconds'] + intent['budget_slice']['limits']['max_seconds']
    limits['advance_seconds'] = deadline - .01
    assert budget.stop_node(root, job, intent, limits, meter) is None
    limits['advance_seconds'] = deadline
    assert budget.stop_node(root, job, intent, limits, meter) == 'node_slice_time_limit'


@pytest.mark.parametrize('role', list('HWAE'))
def test_ordinary_nodes_keep_soft_token_target_and_have_no_final_grant(tmp_path, role):
    root = prepare(tmp_path, max_steps=4)
    job, intent = accept(root, role, final=False)
    assert 'final_return' not in intent['budget_slice']
    assert budget.final_a_return(root, job, intent) is None
    assert budget.final_a_barrier(root) is None
    meter = dict(input_tokens=100, cached_input_tokens=99, output_tokens=1)
    assert budget.stop_node(root, job, intent, budget.snapshot(root), meter) is None


def test_settings_without_final_policy_keep_soft_target_without_final_grant(tmp_path):
    root = prepare(tmp_path, legacy=True)
    settings = (root / 'settings.json').read_bytes()
    job, intent = accept(root, final=False)
    assert 'final_return' not in intent['budget_slice']
    assert budget.final_a_return(root, job, intent) is None
    assert budget.final_a_barrier(root) is None
    assert budget.delivery_reserve(c.settings(root)) is None
    meter = dict(input_tokens=100, cached_input_tokens=99, output_tokens=1)
    assert budget.stop_node(root, job, intent, budget.snapshot(root), meter) is None
    assert (root / 'settings.json').read_bytes() == settings
