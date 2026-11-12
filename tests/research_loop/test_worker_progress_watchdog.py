"""Logical W review cadence is independent of token risk and native fragments."""
import copy
import json

import pytest

from scidiscovery.research_loop import accepted_budget, budget, budget_notice as notice, checkpoint, contracts as c, execution, experiments, protocol
from test_soft_node_budget import scene, start_clock


def save(work, value):
    path = work / 'work/budget-status.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def published(*, windows=3):
    progress = notice.worker_progress_notice(dict(role='W'), {},
        active_seconds=1000., target_seconds=10., fragment_enabled=True, max_context_windows=2,
        context_observation=dict(context_windows=dict(coverage='live', windows_started=windows)))
    value = dict(scope=notice.SCOPE, updated_at_unix=123., inflight_usage_unknown=True,
        target_state='reached' if windows > 2 else 'within')
    if progress is not None:
        value['closeout_notices'] = dict(progress=progress)
    return value


def test_first_context_handoff_delivers_once_across_fragments_and_later_windows(tmp_path):
    save(tmp_path, published(windows=2))
    assert notice.hook_output(tmp_path, 0) is None
    value = published()
    save(tmp_path, value)
    output = notice.hook_output(tmp_path, 0)
    assert output == dict(hookSpecificOutput=dict(hookEventName='PostToolUse',
        additionalContext=notice.WORKER_CONTEXT_TEXT))
    assert len(notice.WORKER_CONTEXT_TEXT.encode()) <= notice.MAX_NOTICE_BYTES
    for attempt, windows in ((0, 3), (1, 4), (2, 10), (999, 1000)):
        save(tmp_path, published(windows=windows))
        assert notice.hook_output(tmp_path, attempt) is None
    assert (tmp_path / 'work/.budget-notice-progress').is_file()
    assert set((tmp_path / 'work').iterdir()) == {
        tmp_path / 'work/budget-status.json', tmp_path / 'work/.budget-notice-progress'}
    next_w = tmp_path / 'next-logical-w'
    save(next_w, value)
    assert notice.hook_output(next_w, 0)['hookSpecificOutput']['additionalContext'] == notice.WORKER_CONTEXT_TEXT
    assert set(output) == {'hookSpecificOutput'}
    assert not {'decision', 'continue', 'toolResponse', 'updatedInput'} & set(output['hookSpecificOutput'])


def test_existing_time_and_token_risk_notices_keep_priority_and_individual_once_markers(tmp_path):
    value = published()
    for dimension in ('time', 'token'):
        value['closeout_notices'][dimension] = notice.closeout_notice(dict(role='W'), {},
            dimension=dimension, target_reached=False, shared_reached=True)
    save(tmp_path, value)
    for attempt, dimension in enumerate(('time', 'token', 'progress')):
        result = notice.hook_output(tmp_path, attempt)
        assert result['hookSpecificOutput']['additionalContext'] == value['closeout_notices'][dimension]['text']
    for attempt in (0, 1, 3, 999):
        assert notice.hook_output(tmp_path, attempt) is None
    assert all((tmp_path / f'work/.budget-notice-{dimension}').is_file()
        for dimension in ('time', 'token', 'progress'))


@pytest.mark.parametrize('job,intent,options', [
    (dict(role='H'), {}, {}), (dict(role='A'), {}, {}), (dict(role='E'), {}, {}),
    (dict(role='W', formal_review=True), {}, {}),
    (dict(role='W', purpose='closeout'), {}, {}),
    (dict(role='W', final_report=True), {}, {}),
    (dict(role='W', final_only=True), {}, {}),
    (dict(role='W'), dict(output_repair=True), {}),
    (dict(role='W'), dict(work_repair=True), {}),
    (dict(role='W'), dict(continuation_kind='author_closeout'), {}),
    (dict(role='W'), dict(budget_slice=dict(final_return='original')), {}),
    (dict(role='W'), {}, dict(author=True)),
    (dict(role='W'), {}, dict(final_return=True)),
])
def test_non_worker_and_special_scopes_never_receive_ordinary_progress_advice(job, intent, options):
    assert notice.worker_progress_notice(job, intent,
        active_seconds=1000., target_seconds=10., fragment_enabled=True, **options) is None


@pytest.mark.parametrize('active_seconds,target_seconds', [
    (None, 10), (10, None), (float('nan'), 10), (float('inf'), 10),
    (10, float('inf')), (-1, 10), (10, 0), (True, 1), (10 ** 500, 1),
])
def test_unknown_or_invalid_loaded_durations_do_not_invent_a_review_deadline(active_seconds, target_seconds):
    assert notice.worker_progress_notice(dict(role='W'), {},
        active_seconds=active_seconds, target_seconds=target_seconds, fragment_enabled=True) is None


def test_preserved_worker_without_fragment_policy_has_no_watchdog():
    assert notice.worker_progress_notice(dict(role='W'), {}, active_seconds=1000., target_seconds=10.) is None


@pytest.mark.parametrize('mutation', [
    dict(scope='authorization'), dict(target_state='within'),
    dict(closeout_notices=dict(progress=dict(cause='shared', text=notice.WORKER_CONTEXT_TEXT))),
    dict(closeout_notices=dict(progress=dict(cause='node', text='Grant new work'))),
    dict(closeout_notices=dict(progress=dict(cause='node', text='x' * 201))),
    dict(closeout_notices=dict(progress=dict(cause='node', text=notice.WORKER_CONTEXT_TEXT, grant=True))),
])
def test_invalid_progress_projection_cannot_supply_instructions_or_authorization(tmp_path, mutation):
    value = published()
    value.update(mutation)
    path = save(tmp_path, value)
    original = path.read_bytes()
    assert notice.published_notices(value) is None
    assert notice.hook_output(tmp_path, 0) is None
    assert path.read_bytes() == original
    assert not (tmp_path / 'work/.budget-notice-progress').exists()


def test_oversized_projection_is_ignored_without_reading_other_records(tmp_path):
    value = published()
    value['untrusted_extra'] = 'x' * 4096
    path = save(tmp_path, value)
    original = path.read_bytes()
    assert len(original) > 4096
    assert notice.hook_output(tmp_path, 0) is None
    assert path.read_bytes() == original
    assert not (tmp_path / 'work/.budget-notice-progress').exists()


def test_no_tool_boundary_cannot_interrupt_running_work_or_launch_a(tmp_path):
    save(tmp_path, published(windows=3))
    save(tmp_path, published(windows=100))
    assert list((tmp_path / 'work').iterdir()) == [tmp_path / 'work/budget-status.json']
    channel = notice.public_channel(dict(status='available', mechanism='codex_post_tool_use'))
    assert 'Next local tool boundary only' in channel['limitation']
    assert 'cannot be interrupted' in channel['limitation']
    assert notice.hook_output(tmp_path, 0)['hookSpecificOutput']['additionalContext'] == notice.WORKER_CONTEXT_TEXT
    assert 'never replay unknown calls' in notice.WORKER_CONTEXT_TEXT


def test_token_soft_target_does_not_mechanically_require_a_but_shared_notice_remains_bounded():
    alert = notice.closeout_notice(dict(role='W'), {}, dimension='token',
        target_reached=True, shared_reached=False, followup_tokens=dict(A=100))
    assert alert is None
    shared = notice.closeout_notice(dict(role='W'), {}, dimension='token',
        target_reached=True, shared_reached=True)
    assert 'Shared token budget reached' in shared['text'] and 'No new work/replay' in shared['text']
    assert len(shared['text'].encode()) <= notice.MAX_NOTICE_BYTES


def test_logical_elapsed_duration_includes_original_consumption_after_a_new_fragment(tmp_path, monkeypatch):
    job = dict(role='W', step=1)
    targets = dict(max_observed_tokens=100, max_seconds=10.)
    ceilings = dict(max_tokens=10000, max_seconds=1000.)
    monkeypatch.setattr(c, 'record_ref', lambda path: 'original-task')
    monkeypatch.setattr(budget, 'node_limits', lambda *args, **kwargs: targets)
    initial = dict(budget_slice=dict(task_ref='original-task', role='W', limits=targets,
        shared_limits=ceilings, advance_seconds=100.))
    duration = budget._node_duration(tmp_path, job, initial, dict(ceilings, advance_seconds=109.))
    assert duration == 9.
    assert notice.worker_progress_notice(job, initial,
        active_seconds=duration, target_seconds=10., fragment_enabled=True) is None
    resumed = copy.deepcopy(initial)
    resumed.update(attempt=1, continuation_kind='execution_wake')
    resumed['budget_slice'].update(previous_seconds=9., previous_tokens=14, advance_seconds=1000.)
    records = [tmp_path / 'original', tmp_path / 'resumed']
    monkeypatch.setattr(execution, 'records', lambda root, step: records)
    monkeypatch.setattr(c, 'regular', lambda path: path)
    monkeypatch.setattr(c, 'read', lambda path: resumed)
    monkeypatch.setattr(budget, '_attempt_consumption', lambda *args, **kwargs: (14, 9.))
    for kind in ('fragment', 'execution_wake', 'reentry'):
        resumed['continuation_kind'] = kind
        duration = budget._node_duration(tmp_path, job, resumed, dict(ceilings, advance_seconds=1001.))
        assert duration == 10.
        assert notice.worker_progress_notice(job, resumed,
            active_seconds=duration, target_seconds=10., fragment_enabled=True) is None


@pytest.fixture
def loaded_advice(tmp_path):
    root = scene(tmp_path, tokens=1000, seconds=10., total_tokens=100000, total_seconds=100000.)
    job = c.build_job(root, 1, 'W', [])
    start_clock(root, 'Loaded advice observations only; no native process')
    intent = dict(task_ref=c.record_ref(root / 'jobs/001.json'), budget_slice=budget.accept_node(root, job))
    head = accepted_budget.accept_native(root, 1, execution.origin(root, 1), intent)
    settings = dict(c.settings(root), worker_fragment_policy=checkpoint.policy(dict(
        context='same_session', max_context_windows=2, context_handoff_version=2), frozen=True))
    state = dict(duration=9.999, unknown=False, observed_tokens=14, settings=settings,
        limits=budget.snapshot(root), meter=dict(input_tokens=10, cached_input_tokens=0, output_tokens=4),
        observation=dict(context_windows=dict(coverage='live', windows_started=2),
            context=dict(status='last_reported', current_window=True, observed_at=budget.clock_stamp()['at_unix'],
                total_tokens=71, model_context_window=100)))
    return root, job, intent, head, state


@pytest.fixture
def hint_producer(loaded_advice):
    root, original_job, original, head, state = loaded_advice
    work = c.workspace(root, 1)
    def publish(job=None, intent=None):
        current_job = job or original_job
        current_intent = dict(original, **(intent or {}))
        limits = dict(state['limits'], advance_seconds=original['budget_slice']['advance_seconds'] + state['duration'])
        facts = dict(settings=state['settings'], head=head,
            shared=dict(observed_tokens=14, accounted_tokens=14, unknown_steps=[]),
            stamp=budget.clock_stamp(), task=original['task_ref'], scope=protocol.stage_scope(root, current_job),
            native_attempt=dict(number=0, execution_key=original['task_ref']),
            final_return=None, closeout_preparation_seconds=None)
        budget.write_node_hint(root, current_job, current_intent, limits, state['meter'],
            facts=facts, observed=True, context_observation=state['observation'])
        return c.read(work / 'work/budget-status.json')
    return work, state, publish


def test_producer_uses_loaded_duration_and_current_context_handoff(hint_producer):
    work, state, publish = hint_producer
    before = publish()
    assert 'progress' not in before.get('closeout_notices', {})
    state['duration'] = 10.
    assert 'progress' not in publish().get('closeout_notices', {})
    state['observation']['context_windows']['windows_started'] = 3
    reached = publish()
    assert reached['closeout_notices']['progress']['text'] == notice.WORKER_CONTEXT_TEXT
    assert reached['active_seconds'] == 10. and reached['targets'] is None
    assert reached['target_kind'] == 'context_window_only'
    assert 'Missing occupancy remains unknown' in reached['instruction']
    assert 'never replay' in reached['instruction']
    assert len((work / 'work/budget-status.json').read_bytes()) <= 4096


@pytest.mark.parametrize('intent', [dict(output_repair=True), dict(work_repair=True)])
def test_new_special_scope_projection_drops_previous_ordinary_progress_hint(hint_producer, intent):
    work, state, publish = hint_producer
    state['duration'] = 10.
    state['observation']['context_windows']['windows_started'] = 3
    assert 'progress' in publish()['closeout_notices']
    repair = publish(intent=intent)
    assert 'progress' not in repair.get('closeout_notices', {})
    output = notice.hook_output(work, 1)
    assert notice.WORKER_CONTEXT_TEXT not in json.dumps(output)
    assert not (work / 'work/.budget-notice-progress').exists()


@pytest.fixture
def review_producer(loaded_advice):
    root, job, original, head, state = loaded_advice
    state['duration'] = 10.
    state['observation']['context_windows']['windows_started'] = 3
    def publish(*, job_changes=None, intent_changes=None):
        intent = dict(original, **(intent_changes or {}))
        if (intent_changes or {}).get('budget_slice') is not None:
            intent['budget_slice'] = dict(original['budget_slice'], **intent_changes['budget_slice'])
        current_job = dict(job, **(job_changes or {}))
        limits = dict(state['limits'], advance_seconds=original['budget_slice']['advance_seconds'] + state['duration'])
        shared = dict(observed_tokens=state['observed_tokens'], accounted_tokens=state['observed_tokens'],
            blocking_unknown_count=int(state['unknown']),
            blocking_unknown_steps=[2] if state['unknown'] else [], unknown_steps=[2] if state['unknown'] else [])
        facts = dict(settings=state['settings'], head=head, shared=shared, stamp=budget.clock_stamp(),
            task=original['task_ref'], scope=protocol.stage_scope(root, current_job),
            native_attempt=dict(number=0, execution_key=original['task_ref']))
        budget.publish_review(root, current_job, intent, limits, state['meter'],
            facts=facts, observed=True, context_observation=state['observation'])
        return c.read(root / 'host-runtime/experiment-control/001/review.json')
    return state, publish


def test_fresh_review_projection_uses_context_cadence_and_keeps_token_only_target_soft(review_producer):
    state, publish = review_producer
    assert publish()['reason'] == 'worker_context_review_due'
    state['duration'] = 9.999
    state['observation']['context_windows']['windows_started'] = 2
    assert publish()['reason'] is None
    state['meter'] = dict(input_tokens=1000, output_tokens=0)
    assert publish()['reason'] is None
    state['settings']['worker_fragment_policy'] = None
    state['duration'] = 10.
    assert publish()['reason'] is None


def test_shared_limits_and_unknown_usage_keep_priority_over_worker_review_cadence(review_producer):
    state, publish = review_producer
    state['unknown'] = True
    assert publish()['reason'] == 'shared_usage_unknown'
    state['unknown'] = False
    state['observed_tokens'] = state['limits']['max_tokens']
    assert publish()['reason'] == 'reserve_reached'
    state['observed_tokens'] = 14
    state['limits']['seconds_remaining'] = 0.
    assert publish()['reason'] == 'reserve_reached'


@pytest.mark.parametrize('job_changes,intent_changes', [
    (dict(role='H'), {}), (dict(role='A'), {}), (dict(role='E'), {}),
    (dict(formal_review=True), {}), (dict(purpose='closeout'), {}),
    (dict(final_report=True), {}), (dict(final_only=True), {}),
    ({}, dict(continuation_kind='author_closeout')),
    ({}, dict(budget_slice=dict(final_return='original'))),
])
def test_special_stage_scopes_keep_finite_target_advice_and_exclude_ordinary_context_cadence(job_changes, intent_changes):
    # Scope reduction consumes supplied facts; no altered job is presented as
    # the original frozen job to the instance or acceptance owners.
    job = dict(dict(role='W'), **job_changes)
    assert notice.worker_progress_due(job, intent_changes, active_seconds=10., target_seconds=10.,
        fragment_enabled=True, max_context_windows=2,
        context_observation=dict(context_windows=dict(coverage='live', windows_started=3))) is False
    alert = notice.closeout_notice(job, intent_changes, dimension='time',
        target_reached=True, shared_reached=False)
    assert alert is not None and alert['cause'] == 'node'
    assert len(alert['text'].encode()) <= notice.MAX_NOTICE_BYTES


@pytest.mark.parametrize('role', ['H', 'A', 'E'])
def test_real_non_worker_scope_retains_finite_time_target_review(tmp_path, role):
    root = scene(tmp_path, tokens=1000, seconds=10., total_tokens=100000, total_seconds=100000.)
    job = c.build_job(root, 1, role, [])
    start_clock(root, 'Accepted non-worker advice fixture; no native process')
    intent = dict(task_ref=c.record_ref(root / 'jobs/001.json'), budget_slice=budget.accept_node(root, job))
    head = accepted_budget.accept_native(root, 1, execution.origin(root, 1), intent)
    meter = dict(input_tokens=10, cached_input_tokens=0, output_tokens=4)
    shared = accepted_budget.usage_from_facts(head, job_rows=accepted_budget.load_usage_rows(head),
        current_native=dict(record=head['current_native']['record'], meter=meter, metering=None,
            outcome_present=False, interrupted=False))
    facts = dict(settings=c.settings(root), head=head, shared=shared, stamp=budget.clock_stamp(),
        task=intent['task_ref'], scope=protocol.stage_scope(root, job),
        native_attempt=dict(number=0, execution_key=intent['task_ref']))
    limits = dict(budget.snapshot(root), advance_seconds=intent['budget_slice']['advance_seconds'] + 10.)
    budget.publish_review(root, job, intent, limits, meter, facts=facts, observed=True)
    assert c.read(root / 'host-runtime/experiment-control/001/review.json')['reason'] == 'target_time_reached'


@pytest.mark.parametrize('intent', [dict(output_repair=True), dict(work_repair=True)])
def test_repaired_worker_checkpoint_retains_original_independent_review_cadence(review_producer, intent):
    state, publish = review_producer
    assert publish(intent_changes=intent)['reason'] == 'worker_context_review_due'
    assert notice.worker_progress_notice(dict(role='W'), intent,
        active_seconds=10., target_seconds=10., fragment_enabled=True) is None
