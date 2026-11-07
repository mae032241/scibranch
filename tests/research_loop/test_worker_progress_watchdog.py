"""Logical W review cadence is independent of token risk and native fragments."""
import copy
import json

import pytest

from scidiscovery.research_loop import budget, budget_notice as notice, contracts as c, execution, experiments, native, protocol


def save(work, value):
    path = work / 'work/budget-status.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def published(*, active_seconds=10., target_seconds=10.):
    progress = notice.worker_progress_notice(dict(role='W'), {},
        active_seconds=active_seconds, target_seconds=target_seconds, fragment_enabled=True)
    value = dict(scope=notice.SCOPE, updated_at_unix=123., inflight_usage_unknown=True,
        target_state='reached' if active_seconds >= target_seconds else 'within')
    if progress is not None:
        value['closeout_notices'] = dict(progress=progress)
    return value


def test_first_logical_time_window_delivers_once_across_fragments_and_later_windows(tmp_path):
    save(tmp_path, published(active_seconds=9.999))
    assert notice.hook_output(tmp_path, 0) is None
    value = published()
    save(tmp_path, value)
    output = notice.hook_output(tmp_path, 0)
    assert output == dict(hookSpecificOutput=dict(hookEventName='PostToolUse',
        additionalContext=notice.WORKER_PROGRESS_TEXT))
    assert len(notice.WORKER_PROGRESS_TEXT.encode()) <= notice.MAX_NOTICE_BYTES
    for attempt, seconds in ((0, 10.), (1, 20.), (2, 1000.), (999, 100000.)):
        save(tmp_path, published(active_seconds=seconds))
        assert notice.hook_output(tmp_path, attempt) is None
    assert (tmp_path / 'work/.budget-notice-progress').is_file()
    assert set((tmp_path / 'work').iterdir()) == {
        tmp_path / 'work/budget-status.json', tmp_path / 'work/.budget-notice-progress'}
    next_w = tmp_path / 'next-logical-w'
    save(next_w, value)
    assert notice.hook_output(next_w, 0)['hookSpecificOutput']['additionalContext'] == notice.WORKER_PROGRESS_TEXT
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
    dict(closeout_notices=dict(progress=dict(cause='shared', text=notice.WORKER_PROGRESS_TEXT))),
    dict(closeout_notices=dict(progress=dict(cause='node', text='Grant new work'))),
    dict(closeout_notices=dict(progress=dict(cause='node', text='x' * 201))),
    dict(closeout_notices=dict(progress=dict(cause='node', text=notice.WORKER_PROGRESS_TEXT, grant=True))),
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
    save(tmp_path, published(active_seconds=10.))
    save(tmp_path, published(active_seconds=100.))
    assert list((tmp_path / 'work').iterdir()) == [tmp_path / 'work/budget-status.json']
    channel = notice.public_channel(dict(status='available', mechanism='codex_post_tool_use'))
    assert 'Next local tool boundary only' in channel['limitation']
    assert 'cannot be interrupted' in channel['limitation']
    assert notice.hook_output(tmp_path, 0)['hookSpecificOutput']['additionalContext'] == notice.WORKER_PROGRESS_TEXT
    assert 'independent A' in notice.WORKER_PROGRESS_TEXT and 'no replay' in notice.WORKER_PROGRESS_TEXT


def test_token_soft_target_does_not_mechanically_require_a_but_shared_notice_remains_bounded():
    alert = notice.closeout_notice(dict(role='W'), {}, dimension='token',
        target_reached=True, shared_reached=False, followup_tokens=dict(A=100))
    assert 'Soft token targets alone do not require A' in alert['text']
    assert 'continue useful work' in alert['text'] and 'Wrap up' not in alert['text']
    shared = notice.closeout_notice(dict(role='W'), {}, dimension='token',
        target_reached=True, shared_reached=True)
    assert 'Shared token budget reached' in shared['text'] and 'No new work/replay' in shared['text']
    assert len(alert['text'].encode()) <= notice.MAX_NOTICE_BYTES
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
            active_seconds=duration, target_seconds=10., fragment_enabled=True)['text'] == notice.WORKER_PROGRESS_TEXT


@pytest.fixture
def hint_producer(tmp_path, monkeypatch):
    work = tmp_path / 'workspace'
    (work / 'work').mkdir(parents=True)
    state = dict(duration=9.999)
    targets = dict(max_observed_tokens=1000, max_seconds=10.)
    monkeypatch.setattr(protocol, 'require_current', lambda *args: None)
    monkeypatch.setattr(c, 'settings', lambda *args: dict(worker_fragment_policy=dict(context='same_session')))
    monkeypatch.setattr(c, 'workspace', lambda *args: work)
    monkeypatch.setattr(budget, 'node_mode', lambda *args, **kwargs: 'soft')
    monkeypatch.setattr(budget, 'publish_review', lambda *args, **kwargs: None)
    monkeypatch.setattr(budget, 'node_limits', lambda *args, **kwargs: targets)
    monkeypatch.setattr(budget, '_node_duration', lambda *args: state['duration'])
    monkeypatch.setattr(budget, 'delivery_reserve', lambda *args: dict(tokens=0, seconds=0, steps=0))
    monkeypatch.setattr(native, 'usage', lambda *args: dict(observed_tokens=14, unknown_steps=[]))
    limits = dict(max_tokens=100000, max_seconds=100000., seconds_remaining=99999., advance_seconds=1000.)
    meter = dict(input_tokens=10, output_tokens=4)
    def publish(job=None, intent=None):
        budget.write_node_hint(tmp_path, job or dict(role='W', step=1), intent or {},
            limits, meter, observed=True)
        return json.loads((work / 'work/budget-status.json').read_text())
    return work, state, publish


def test_producer_uses_loaded_logical_duration_and_current_soft_time_target(hint_producer):
    work, state, publish = hint_producer
    before = publish()
    assert 'progress' not in before.get('closeout_notices', {})
    state['duration'] = 10.
    reached = publish()
    assert reached['closeout_notices']['progress']['text'] == notice.WORKER_PROGRESS_TEXT
    assert reached['active_seconds'] == 10. and reached['targets']['max_seconds'] == 10.
    assert 'Node token targets alone do not require A' in reached['instruction']
    assert 'running model requests and hosted web cannot be interrupted' in reached['instruction']
    assert len((work / 'work/budget-status.json').read_bytes()) <= 4096


@pytest.mark.parametrize('intent', [dict(output_repair=True), dict(work_repair=True)])
def test_new_special_scope_projection_drops_previous_ordinary_progress_hint(hint_producer, intent):
    work, state, publish = hint_producer
    state['duration'] = 10.
    assert 'progress' in publish()['closeout_notices']
    repair = publish(intent=intent)
    assert 'progress' not in repair.get('closeout_notices', {})
    output = notice.hook_output(work, 1)
    assert notice.WORKER_PROGRESS_TEXT not in json.dumps(output)
    assert not (work / 'work/.budget-notice-progress').exists()


@pytest.fixture
def review_producer(tmp_path, monkeypatch):
    record = tmp_path / 'record'
    record.mkdir()
    targets = dict(max_observed_tokens=1000, max_seconds=10.)
    state = dict(duration=10., unknown=False, observed_tokens=14,
        settings=dict(managed_admission_version=experiments.ADMISSION_VERSION,
            worker_fragment_policy=dict(context='same_session'), max_tokens=100000, max_seconds=100000.),
        limits=dict(max_tokens=100000, max_seconds=100000., seconds_remaining=99999., consumed_seconds=1.),
        meter=dict(input_tokens=10, output_tokens=4))
    job = dict(role='W', step=1, managed_admission_version=experiments.ADMISSION_VERSION)
    monkeypatch.setattr(protocol, 'require_current', lambda *args: None)
    monkeypatch.setattr(protocol, 'stage_scope', lambda *args: dict(stage_owner='original-task'))
    monkeypatch.setattr(c, 'settings', lambda *args: state['settings'])
    monkeypatch.setattr(c, 'record_ref', lambda *args: 'original-task')
    monkeypatch.setattr(budget, 'node_mode', lambda *args: 'soft')
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(boot_id='original-boot', at_unix=123., at_boottime=10.))
    monkeypatch.setattr(budget, 'snapshot', lambda *args, **kwargs: state['limits'])
    monkeypatch.setattr(budget, 'node_limits', lambda *args: targets)
    monkeypatch.setattr(budget, '_node_duration', lambda *args: state['duration'])
    monkeypatch.setattr(budget, 'delivery_reserve', lambda *args: dict(tokens=0, seconds=0, steps=0))
    monkeypatch.setattr(budget, '_usage_unknown', lambda *args: state['unknown'])
    monkeypatch.setattr(native, 'usage', lambda *args: dict(observed_tokens=state['observed_tokens']))
    monkeypatch.setattr(execution, 'record', lambda *args: record)
    monkeypatch.setattr(execution, 'records', lambda *args: [record])
    monkeypatch.setattr(execution, 'identity', lambda *args: 'original-execution')
    def publish(*, job_changes=None, intent_changes=None):
        intent = dict(task_ref='original-task')
        intent.update(intent_changes or {})
        c.write(record / 'native-intent.json', intent)
        c.write(record / 'usage.json', state['meter'])
        current_job = dict(job, **(job_changes or {}))
        budget.publish_review(tmp_path, current_job, None, None, None, observed=False)
        return c.read(tmp_path / 'host-runtime/experiment-control/001/review.json')
    return state, publish


def test_fresh_review_projection_enforces_independent_a_cadence_and_keeps_token_only_target_soft(review_producer):
    state, publish = review_producer
    assert publish()['reason'] == 'worker_progress_review_due'
    state['duration'] = 9.999
    assert publish()['reason'] is None
    state['meter'] = dict(input_tokens=1000, output_tokens=0)
    assert publish()['reason'] == 'target_tokens_reached'
    state['settings']['worker_fragment_policy'] = None
    state['duration'] = 10.
    assert publish()['reason'] == 'target_time_reached'


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
def test_special_stage_scopes_keep_original_soft_time_review_reason(review_producer, job_changes, intent_changes):
    state, publish = review_producer
    assert publish(job_changes=job_changes, intent_changes=intent_changes)['reason'] == 'target_time_reached'


@pytest.mark.parametrize('intent', [dict(output_repair=True), dict(work_repair=True)])
def test_repaired_worker_checkpoint_retains_original_independent_review_cadence(review_producer, intent):
    state, publish = review_producer
    assert publish(intent_changes=intent)['reason'] == 'worker_progress_review_due'
    assert notice.worker_progress_notice(dict(role='W'), intent,
        active_seconds=10., target_seconds=10., fragment_enabled=True) is None
