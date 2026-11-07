"""Unlimited globals retain actual accounting and finite original task windows.

The synthetic originals use the production acceptance and sealing paths; no
native process, provider or scientific experiment is launched by this module.
"""
import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, execution, native
from test_final_a_completion_contract import accept, complete
from test_history_handoff import finish
from test_node_budget import policy


def instance(tmp_path, monkeypatch, *, max_tokens=None, max_seconds=None, max_steps=3, node_targets=True):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain exact findings and missing evidence under explicit global limits.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'],
        max_tokens=max_tokens, max_seconds=max_seconds, max_steps=max_steps,
        model='fixture-no-model', reasoning_effort='high', search_policy=None,
        node_budget_policy=policy(tokens=100, seconds=10) if node_targets else None)
    now = [100.]
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(
        at_unix=now[0], at_boottime=now[0], boot_id='unlimited-budget-fixture'))
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', 'Synthetic control fixture begins')
    return root, now


def accepted_h(root, *, tokens):
    """Publish an explicit synthetic high-usage original without a process."""
    job = c.build_job(root, 1, 'H', [])
    record = execution.origin(root, 1)
    intent = dict(step=1, task_ref=c.record_ref(root / 'jobs/001.json'),
        budget_slice=budget.accept_node(root, job))
    accepted_budget.accept_native(root, 1, record, intent)
    payload = dict(summary='Synthetic bounded finding', scientific_result='No scientific experiment was run.',
        limitations=['Deterministic control fixture only'], implementation_selection=None,
        search_plan=None, next_action='analyze', route_reason='Independent assessment of retained limitations')
    c.write(record / 'science-output.json', payload)
    c.write(record / 'usage.json', dict(input_tokens=tokens, cached_input_tokens=tokens - 1, output_tokens=3))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None, native_started=False,
        usage=native.usage(root), raw_ref=c.file_ref(record / 'science-output.json')))
    c.seal_result(root, 1)
    return record


def test_unlimited_snapshot_and_admission_retain_arbitrarily_large_reported_consumption(tmp_path, monkeypatch):
    root, now = instance(tmp_path, monkeypatch)
    original_settings = (root / 'settings.json').read_bytes()
    tokens = 10**30
    record = accepted_h(root, tokens=tokens)
    originals = {path: path.read_bytes() for path in (record / 'usage.json', record / 'native-outcome.json')}
    now[0] += 10**12

    limits = budget.snapshot(root)
    usage = native.usage(root)
    assert limits['max_tokens'] is limits['max_seconds'] is limits['seconds_remaining'] is None
    assert limits['consumed_seconds'] == limits['advance_seconds'] == 10**12
    assert usage['observed_tokens'] == usage['accounted_tokens'] == tokens + 3
    assert usage['cached_input_tokens'] == tokens - 1  # Included in input once.
    job = c.build_job(root, 2, 'A', [1])
    assert budget.node_admission(root, job) is None
    assert budget.execution_available(root, job, limits, usage) == dict(seconds=None, tokens=None)
    anchor = budget.accept_node(root, job)
    assert anchor['shared_limits'] == dict(max_tokens=None, max_seconds=None)
    assert anchor['limits'] == dict(max_observed_tokens=100, max_seconds=10)
    assert (root / 'settings.json').read_bytes() == original_settings
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    events, _ = budget.audit_events(root)
    assert budget._snapshot(root, c.settings(root), events)['seconds_remaining'] is None


@pytest.mark.parametrize('max_tokens,max_seconds,tokens,seconds,reason', [
    (None, 20, 10**30, 21, 'node_budget_unavailable_seconds'),
    (20, None, 20, 10**12, 'node_budget_unavailable_tokens'),
    (20, 20, 20, 0, 'node_budget_unavailable_tokens'),
    (20, 20, 7, 20, 'node_budget_unavailable_seconds'),
    (20, 20, 7, 19, None),
])
def test_each_finite_dimension_still_controls_new_admission(
        tmp_path, monkeypatch, max_tokens, max_seconds, tokens, seconds, reason):
    root, now = instance(tmp_path, monkeypatch, max_tokens=max_tokens, max_seconds=max_seconds)
    accepted_h(root, tokens=tokens)
    now[0] += seconds
    job = c.build_job(root, 2, 'A', [1])
    assert budget.node_admission(root, job) == reason
    available = budget.execution_available(root, job, budget.snapshot(root), native.usage(root))
    assert (available['tokens'] is None) == (max_tokens is None)
    assert (available['seconds'] is None) == (max_seconds is None)


def test_unlimited_advice_has_no_shared_risk_but_retains_finite_target_notices(tmp_path, monkeypatch):
    root, now = instance(tmp_path, monkeypatch)
    job = c.build_job(root, 1, 'H', [])
    intent = dict(step=1, task_ref=c.record_ref(root / 'jobs/001.json'),
        budget_slice=budget.accept_node(root, job))
    record = execution.origin(root, 1)
    accepted_budget.accept_native(root, 1, record, intent)
    meter = dict(input_tokens=100, cached_input_tokens=99, output_tokens=3)
    c.write(record / 'usage.json', meter)
    budget.write_node_hint(root, job, intent, budget.snapshot(root), meter, observed=True)

    hint = c.read(c.workspace(root, 1) / 'work/budget-status.json')
    assert hint['shared_observed_tokens_remaining_upper_bound'] is None
    assert hint['shared_active_seconds_remaining'] is None
    assert hint['current_work_active_seconds_available'] is None
    assert hint['current_work_observed_tokens_remaining_upper_bound'] is None
    assert hint['reserve_state'] == 'within'
    assert hint['closeout_notices']['token']['cause'] == 'node'
    assert set(hint['closeout_notices']) == {'token'}
    review = c.read(root / 'host-runtime/experiment-control/001/review.json')
    assert review['deadline_boottime'] is None
    assert review['valid_until_boottime'] == now[0] + 10
    assert review['reason'] == 'target_tokens_reached'


def test_step_exhaustion_final_has_finite_completion_window_without_global_caps(tmp_path, monkeypatch):
    root, now = instance(tmp_path, monkeypatch, max_steps=1)
    finish(root, 1, 'H', [], action='analyze')
    assert budget.used_steps(root) == budget.effective_max_steps(root) == 1
    assert not budget.task_step_available(root, 2)
    budget.begin_closeout(root, 1, 2, 'step_limit', final_only=True)
    job, intent = accept(root)
    grant = budget.final_a_return(root, job, intent)
    assert grant['selection']['seconds_remaining'] is None
    assert grant['selection']['used_steps'] == 2
    assert grant['anchor']['limits']['max_seconds'] == 10
    # The shared contract fixture writes its intent only. Match production's
    # native acceptance before publishing a meter for this exact attempt.
    accepted_budget.accept_native(root, job['step'], execution.origin(root, job['step']), intent)
    meter, outcome = complete(root, job, intent)
    receipt = outcome['final_a_completion']['final_overrun']
    assert receipt['accounted_shared_tokens'] == 1220
    assert native.usage(root)['accounted_tokens'] == 1220
    assert receipt['original_shared_max_tokens'] is receipt['shared_tokens_over_ceiling'] is None
    assert receipt['local_tokens_over_target'] == 1110
    assert budget.validate_final_a_receipt(root, job, intent, outcome, meter)['step'] == 2
    assert c.seal_result(root, 2)['next_action'] == 'finish'
    assert native.usage(root)['accounted_tokens'] == 1220
    now[0] += 10
    assert budget.stop_node(root, job, intent, budget.snapshot(root), meter) == 'node_slice_time_limit'
    assert budget.final_a_barrier(root) == grant
    events, _ = budget.audit_events(root)
    assert events[-1]['kind'] == 'scientific_seal'
    assert not (root / 'jobs/003.json').exists()


def test_explicit_unlimited_amendments_keep_consumption_and_existing_reduction_semantics(tmp_path, monkeypatch):
    root, now = instance(tmp_path, monkeypatch, max_tokens=1000, max_seconds=200)
    original_settings = (root / 'settings.json').read_bytes()
    now[0] += 15
    budget.transition(root, 'paused', 'Synthetic original interval completed')
    result = budget.amend(root, max_tokens='unlimited', max_seconds='unlimited',
        authorization_note='Operator explicitly removes both global ceilings')
    assert result['after'] == dict(max_tokens=None, max_seconds=None)
    events, _ = budget._events(root)
    assert budget._threshold_raised(events, 0)
    audited, _ = budget.audit_events(root)
    assert budget._threshold_raised(audited, 0)
    assert budget.snapshot(root)['consumed_seconds'] == 15
    assert budget.snapshot(root)['seconds_remaining'] is None

    changed = budget.amend(root, max_seconds=30, authorization_note='Operator restores an explicit time ceiling')
    assert changed['after'] == dict(max_tokens=None, max_seconds=30)  # None argument means unchanged.
    assert budget.snapshot(root)['seconds_remaining'] == 15
    changed = budget.amend(root, max_tokens=5, max_seconds=7,
        authorization_note='Operator explicitly reduces future authorization')
    assert changed['before'] == dict(max_tokens=None, max_seconds=30)
    assert changed['after'] == dict(max_tokens=5, max_seconds=7)
    events, _ = budget._events(root)
    assert not budget._threshold_raised(events, 0)
    assert budget.snapshot(root)['seconds_remaining'] == 0
    assert budget.snapshot(root)['consumed_seconds'] == 15
    assert (root / 'settings.json').read_bytes() == original_settings
    budget.audit_events(root)


@pytest.mark.parametrize('changes', [dict(max_tokens='unlimited'), dict(max_seconds='unlimited')])
def test_direct_unlimited_amendment_requires_finite_targets_before_publication(tmp_path, monkeypatch, changes):
    root, _ = instance(tmp_path, monkeypatch, max_tokens=1000, max_seconds=200, node_targets=False)
    before = accepted_budget.read(root)
    with pytest.raises(ValueError, match='Unlimited global budgets require explicit finite all-role node budget targets'):
        budget.amend(root, **changes, authorization_note='Operator removes one explicit global ceiling')
    after = accepted_budget.read(root)
    assert after['seq'] == before['seq']
    assert after['head_ref'] == before['head_ref']
    assert budget.ceilings(root) == dict(max_tokens=1000, max_seconds=200, max_steps=3)
    assert not (root / 'control-ledger' / f'{before["seq"] + 1:06d}.json').exists()
    result = budget.amend(root, max_tokens=2000, authorization_note='Finite legacy amendments remain supported')
    assert result['after'] == dict(max_tokens=2000, max_seconds=200)


def test_unlimited_caps_do_not_resolve_an_unknown_original_or_grant_amendment(tmp_path, monkeypatch):
    root, now = instance(tmp_path, monkeypatch)
    job = c.build_job(root, 1, 'H', [])
    record = execution.origin(root, 1)
    intent = dict(step=1, task_ref=c.record_ref(root / 'jobs/001.json'),
        budget_slice=budget.accept_node(root, job))
    accepted_budget.accept_native(root, 1, record, intent)
    original = (record / 'native-intent.json').read_bytes()
    now[0] += 10**12
    assert native.usage(root)['unknown_steps'] == [1]
    assert budget.execution_blocked(root)
    with pytest.raises(RuntimeError, match='running or unknown original execution'):
        budget.amend(root, max_tokens=100, authorization_note='A ceiling change cannot reconcile the original')
    assert budget.snapshot(root)['seconds_remaining'] is None
    assert (record / 'native-intent.json').read_bytes() == original
    assert not (record / 'usage.json').exists()
    assert not (record / 'continuations').exists()
