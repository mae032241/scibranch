"""Current CLI freezes soft node targets; original work survives token crossings."""
import json
from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import accepted_budget, budget, checkpoint, cli, contracts as c, controller, execution, experiments, native, protocol
from scidiscovery.research_loop.protocol import CURRENT
from test_history_handoff import finish
from test_soft_node_budget import FAKE, scene, start_clock


def prepare_cli(tmp_path, *, total_tokens=1000, node_tokens=10):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain each original scientific conclusion and all actual token usage.')
    launcher = tmp_path / 'native.py'
    launcher.write_text(FAKE)
    c.write(launcher.with_suffix('.json'), dict(mode='normal', resume=False, sleep=.15))
    policy = tmp_path / 'nodes.json'
    c.write(policy, {role: dict(max_observed_tokens=node_tokens, max_seconds=20) for role in 'HWAE'})
    search_policy = tmp_path / 'search.json'
    c.write(search_policy, None)
    root = tmp_path / 'instance'
    argv = ['prepare', '--root', str(root), '--objective', str(objective), '--public-root', str(public),
            '--native-launcher-json', json.dumps([sys.executable, str(launcher)]), '--model', 'no-model-fixture',
            '--max-tokens', str(total_tokens), '--max-seconds', '30', '--max-steps', '6',
            '--node-budget-policy', str(policy), '--search-policy', str(search_policy)]
    assert cli.main(argv) == 0
    assert c.settings(root)['node_budget_mode'] == 'soft'
    assert c.settings(root)['handoff_protocol'] == CURRENT
    return root, argv


@pytest.mark.parametrize('role', list('HWAE'))
@pytest.mark.process_e2e
def test_cli_to_each_role_preserves_original_output_after_token_target(tmp_path, role):
    root, _ = prepare_cli(tmp_path)
    step = 1 if role == 'H' else 2
    if step == 2:
        finish(root, 1, 'H', [], action={'W': 'experiment', 'A': 'analyze', 'E': 'extract'}[role])
    c.build_job(root, step, role, [1] if step == 2 else [])
    frozen = (root / 'settings.json').read_bytes()
    start_clock(root, 'Current CLI to native soft token target')
    native.execute_native(root, step)
    outcome = c.read(execution.record(root, step) / 'native-outcome.json')
    assert outcome['stop_reason'] is None and outcome['returncode'] == 0
    assert outcome['budget_slice']['observed_tokens'] == 15
    assert outcome['budget_slice']['limits']['max_observed_tokens'] == 10
    assert c.validate(c.workspace(root, step))['summary'] == 'Original bounded finding'
    hint = c.read(c.workspace(root, step) / 'work/budget-status.json')
    assert hint['mode'] == 'soft' and hint['target_kind'] == 'soft_target'
    assert hint['target_state'] == 'reached'
    assert native.usage(root)['observed_tokens'] >= 15
    assert (root / 'settings.json').read_bytes() == frozen


@pytest.mark.process_e2e
def test_positive_shared_balance_does_not_prepay_node_and_next_node_is_blocked(tmp_path):
    root, _ = prepare_cli(tmp_path, total_tokens=11, node_tokens=1000)
    job = c.build_job(root, 1, 'H', [])
    start_clock(root, 'Positive actual balance admits original work without prepaying its target')
    assert budget.node_admission(root, job) is None
    native.execute_native(root, 1)
    assert c.validate(c.workspace(root, 1))['summary'] == 'Original bounded finding'
    assert c.read(execution.record(root, 1) / 'native-outcome.json')['stop_reason'] is None
    assert native.usage(root)['accounted_tokens'] == 15
    assert budget.snapshot(root)['max_tokens'] == 11
    assert budget.node_admission(root, dict(step=2, role='W')) == 'node_budget_unavailable_tokens'


def test_cli_and_prepare_cannot_create_hard_token_nodes(tmp_path):
    root, argv = prepare_cli(tmp_path)
    with pytest.raises(SystemExit) as error:
        cli.main([*argv, '--node-budget-mode', 'hard'])
    assert error.value.code == 2
    public = tmp_path / 'other-public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Do not create a hard token node.')
    other = tmp_path / 'hard-instance'
    with pytest.raises(ValueError, match='soft mode'):
        controller.prepare(other, objective, public, ['never-launch'], max_tokens=100,
                           max_seconds=10, max_steps=2, model='no-model', reasoning_effort='high',
                           node_budget_mode='hard')
    assert not other.exists()
    assert c.settings(root)['node_budget_mode'] == 'soft'


def test_w_token_crossing_keeps_review_optional_without_stopping_original_node(tmp_path):
    root = scene(tmp_path, tokens=10, seconds=20, search_policy=None)
    finish(root, 1, 'H', [], action='experiment')
    job = c.build_job(root, 2, 'W', [1])
    start_clock(root, 'Original W reaches soft token target')
    record = execution.record(root, 2)
    intent = dict(task_ref=c.record_ref(root / 'jobs/002.json'), budget_slice=budget.accept_node(root, job))
    accepted_budget.accept_native(root, 2, record, intent)
    meter = dict(input_tokens=12, cached_input_tokens=5, output_tokens=3)
    c.write(record / 'usage.json', meter)
    admission = budget.review_configuration(root, job)
    projection = c.read(Path(admission['directory']) / 'review.json')
    config = dict(admission=admission, scope=projection['scope'], native_attempt=projection['native_attempt'])
    assert experiments.review_status(config)['reason'] is None
    assert experiments.review_status(config)['requested'] is False
    assert projection['targets'] is None
    assert experiments.review_status(config)['enforced'] is True  # Host channel remains enforced even with no review due.
    assert budget.stop_node(root, job, intent, budget.snapshot(root), meter) is None


@pytest.mark.parametrize('coverage,windows,ratio,expected',[
    ('live',1,.99,None),('live',3,.85,'worker_context_review_due'),
    ('unknown',1,.1,None)])
def test_context_policy_replaces_w_time_and_token_review_but_preserves_shared_budget(
        tmp_path, coverage, windows, ratio, expected):
    root = scene(tmp_path, tokens=10, seconds=20, search_policy=None)
    finish(root, 1, 'H', [], action='experiment')
    job = c.build_job(root, 2, 'W', [1])
    start_clock(root, 'Observe W context cadence beyond the legacy time target')
    record = execution.record(root, 2)
    intent = dict(task_ref=c.record_ref(root / 'jobs/002.json'), budget_slice=budget.accept_node(root, job))
    accepted_budget.accept_native(root, 2, record, intent)
    meter = dict(input_tokens=12, cached_input_tokens=5, output_tokens=3)
    c.write(record / 'usage.json', meter)
    c.write(record / 'usage-observation.json', dict(context_windows=dict(
        coverage=coverage, windows_started=windows), context=dict(status='last_reported',
        total_tokens=int(ratio * 100), model_context_window=100, current_window=True,
        observed_at=budget.clock_stamp()['at_unix'])))
    head = accepted_budget.read(root)
    shared = accepted_budget.usage_from_facts(head, job_rows=accepted_budget.load_usage_rows(head),
        current_native=dict(record=head['current_native']['record'], meter=meter,
            metering=None, outcome_present=False, interrupted=False))
    facts = dict(settings=dict(c.settings(root),
            worker_fragment_policy=checkpoint.policy(dict(context='same_session',
                max_context_windows=2, context_handoff_version=2), frozen=True)),
        head=head, shared=shared, stamp=budget.clock_stamp(), task=intent['task_ref'],
        scope=protocol.stage_scope(root, job),
        native_attempt=dict(number=0, execution_key=intent['task_ref']))
    limits = dict(budget.snapshot(root), advance_seconds=intent['budget_slice']['advance_seconds'] + 901.)
    observation = c.read(record / 'usage-observation.json')
    budget.publish_review(root, job, intent, limits, meter, facts=facts, observed=True,
        context_observation=observation)
    admission = dict(version=experiments.ADMISSION_VERSION, mode='soft',
        directory=str(root / 'host-runtime/experiment-control/002'), stage_anchor=intent['task_ref'])
    projection = c.read(Path(admission['directory']) / 'review.json')
    config = dict(admission=admission, scope=projection['scope'], native_attempt=projection['native_attempt'])
    review = experiments.review_status(config)
    assert review['reason'] == expected and review['requested'] is bool(expected)
    if expected:
        assert review['enforced'] is True
    assert budget.stop_node(root, job, intent, budget.snapshot(root), meter) is None


def test_author_token_hint_is_soft_while_finite_author_time_stays_bounded(tmp_path):
    root = scene(tmp_path, tokens=1000, seconds=20, search_policy=None)
    job = c.build_job(root, 1, 'H', [])
    start_clock(root, 'Verified author allowance advisory projection')
    intent = dict(task_ref=c.record_ref(root / 'jobs/001.json'), budget_slice=budget.accept_node(root, job),
                  continuation_kind='author_closeout')
    intent['budget_slice'].update(previous_tokens=50, previous_seconds=2.)
    # A pure advice cut supplies the prior original's consumption explicitly;
    # it does not accept or launch an author continuation.
    head = accepted_budget.read(root)
    facts = dict(settings=c.settings(root), head=head,
        shared=dict(observed_tokens=65, accounted_tokens=65, unknown_steps=[]),
        stamp=budget.clock_stamp(), task=intent['task_ref'], scope=protocol.stage_scope(root, job),
        native_attempt=dict(number=0, execution_key=intent['task_ref']),
        final_return=None, closeout_preparation_seconds=None)
    limits = dict(budget.snapshot(root), advance_seconds=intent['budget_slice']['advance_seconds'] + 5.)
    budget.write_node_hint(root, job, intent, limits,
                           dict(input_tokens=12, cached_input_tokens=5, output_tokens=3), facts=facts, observed=True,
                           author_limits=dict(max_observed_tokens=10, max_seconds=8))
    hint = c.read(c.workspace(root, 1) / 'work/budget-status.json')
    assert hint['target_kind'] == 'soft_target' and hint['target_state'] == 'reached'
    assert hint['current_work_observed_tokens_remaining_upper_bound'] == 935
    assert hint['current_work_active_seconds_available'] == 3
