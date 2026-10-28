"""Persisted session-terminal corrections are derived without rewriting evidence."""
import copy
import json

import pytest

from scidiscovery.research_loop import budget, contracts as c, continuation, native, execution, role_share


BASELINE = dict(input_tokens=2017365, cached_input_tokens=1926656, output_tokens=22940)
TERMINAL = dict(input_tokens=2385841, cached_input_tokens=2180096, output_tokens=24982)
DELTA = dict(input_tokens=368476, cached_input_tokens=253440, output_tokens=2042)


@pytest.mark.parametrize('terminal_scope', ['session', 'turn'])
@pytest.mark.process_e2e
def test_real_resume_transport_counts_attempt_delta_and_retains_raw_terminal(tmp_path, terminal_scope):
    from test_worker_resume import FAKE, instance, fail, wait_runner
    counter = lambda value: json.dumps(dict(type='event_msg', payload=dict(type='token_count',
        info=dict(total_token_usage=value)))) + '\n'
    fake = FAKE.replace('rollout.jsonl', 'rollout-' + '11111111-2222-4333-8444-555555555555' + '.jsonl')
    fake = fake.replace("print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':3,'output_tokens':4}}),flush=True)",
        "with p.open('a') as stream:stream.write(" + repr(counter(BASELINE)) + ")\n "
        + "print(json.dumps({'type':'turn.completed','usage':" + repr(BASELINE) + "}),flush=True)")
    terminal = TERMINAL if terminal_scope == 'session' else DELTA
    fake = fake.replace("print(json.dumps({'type':'turn.completed','usage':{'input_tokens':20,'cached_input_tokens':5,'output_tokens':2}}),flush=True)",
        "with (home/'sessions/rollout-11111111-2222-4333-8444-555555555555.jsonl').open('a') as stream:stream.write(" + repr(counter(TERMINAL)) + ")\n"
        + "print(json.dumps({'type':'turn.completed','usage':" + repr(terminal) + "}),flush=True)")
    root = instance(tmp_path, fake=fake, cap=5000000, max_tokens=5000000)
    first = fail(root)
    assert c.read(first / 'usage.json') == BASELINE
    originals = {path: path.read_bytes() for path in first.iterdir() if path.is_file()}
    # The controller entry also schedules a later FINAL A. Exercise the exact
    # native continuation entry here, including admission, process and seal.
    result = continuation.start(root, 1)
    assert result['role'] == 'H'
    current = wait_runner(root)
    assert c.read(current / 'usage.json') == DELTA
    observation = c.read(current / 'native-outcome.json')['metering']
    assert observation['coverage'] == 'terminal' and observation['reconciliation'] == 'matched'
    assert observation['live_tokens'] == DELTA
    raw = [json.loads(line) for line in (current / 'native.jsonl').read_text().splitlines()]
    assert next(event['usage'] for event in raw if event['type'] == 'turn.completed') == terminal
    assert native.usage(root)['observed_tokens'] == 2410823
    assert budget._attempt_consumption(root, c.read(root / 'jobs/001.json'), execution.records(root, 1))[0] == 2410823
    assert (c.workspace(root, 1) / 'work/launches').read_text() == 'exec\nresume\n'
    assert not (root / 'jobs/002.json').exists()
    assert budget.used_steps(root) == 2
    assert all(path.read_bytes() == data for path, data in originals.items())


def old_record(tmp_path):
    record = tmp_path / 'continuations/001'
    record.mkdir(parents=True)
    intent = dict(attempt=1, session=dict(thread_id='exact-thread', path='/retained/session.jsonl'))
    metadata = dict(source='turn.completed', thread_id='exact-thread', coverage='terminal',
        live_coverage='live', reconciliation='terminal_covers_live', has_observation=True,
        baseline_tokens=BASELINE, live_tokens=DELTA, tokens=TERMINAL)
    c.write(record / 'native-intent.json', intent)
    c.write(record / 'usage.json', TERMINAL)
    c.write(record / 'usage-observation.json', metadata)
    c.write(record / 'native-outcome.json', dict(metering=metadata, native_backend='codex',
        thread_id='exact-thread', native_identity=dict(path='/retained/session.jsonl')))
    (record / 'native.jsonl').write_text('Retained original native terminal bytes.\n')
    (record / 'result-identity.json').write_text('Retained seal bytes.\n')
    return record, metadata


def test_old_session_terminal_is_corrected_once_with_all_originals_unchanged(tmp_path):
    record, _ = old_record(tmp_path)
    originals = {path: path.read_bytes() for path in record.iterdir()}
    assert native.attempt_usage(record) == DELTA
    assert native.attempt_usage(record) == DELTA
    total = {key: BASELINE[key] + native.attempt_usage(record)[key] for key in BASELINE}
    assert total == TERMINAL
    assert total['input_tokens'] + total['output_tokens'] == 2410823
    assert DELTA['input_tokens'] + DELTA['output_tokens'] == 370518
    assert all(path.read_bytes() == data for path, data in originals.items())


def test_shared_fold_retains_unknown_and_counts_proven_resume_delta(tmp_path, monkeypatch):
    from test_node_budget import instance
    root = instance(tmp_path, max_tokens=5000000)
    c.build_job(root, 1, 'H', [])
    original = execution.origin(root, 1)
    c.write(original / 'native-intent.json', dict(step=1, task_ref=c.record_ref(root / 'jobs/001.json')))
    c.write(original / 'usage.json', BASELINE)
    c.write(original / 'native-outcome.json', dict(metering=dict(coverage='unknown')))
    resumed, _ = old_record(original)
    # Isolate the consumption fold; continuation authorization is tested through
    # the existing execution-chain suite rather than forged for this fixture.
    monkeypatch.setattr(execution, 'records', lambda selected_root, step: [original, resumed])
    originals = {path: path.read_bytes() for record in (original, resumed)
                 for path in record.iterdir() if path.is_file()}
    result = native.usage(root)
    assert {key: result[key] for key in TERMINAL} == TERMINAL
    assert result['observed_tokens'] == result['accounted_tokens'] == 2410823
    assert result['unknown_steps'] == [1]
    assert all(path.read_bytes() == data for path, data in originals.items())


def test_saved_ui_uses_effective_meter_and_memo_invalidation_without_control_reads(tmp_path, monkeypatch):
    from scidiscovery.research_loop import ui_saved_status
    from test_node_budget import instance
    root = instance(tmp_path, max_tokens=5000000)
    c.build_job(root, 1, 'H', [])
    original = execution.origin(root, 1)
    c.write(original / 'native-intent.json', dict(step=1, task_ref=c.record_ref(root / 'jobs/001.json')))
    c.write(original / 'usage.json', BASELINE)
    c.write(original / 'native-outcome.json', dict(metering=dict(coverage='unknown')))
    resumed, metadata = old_record(original)
    # The display memo uses the saved seal marker, without validating science.
    c.write(original / 'result-identity.json', {})
    monkeypatch.setattr(execution, 'records', lambda selected_root, step: [original, resumed])
    originals = {path: path.read_bytes() for record in (original, resumed)
                 for path in record.iterdir() if path.is_file()}
    control_usage = native.usage
    expected = control_usage(root)
    def forbidden(*args, **kwargs):
        pytest.fail('Saved UI metering must not invoke control admission/accounting')
    monkeypatch.setattr(native, 'usage', forbidden)
    original_read = c.read
    reads = []
    def counted(path, *args, **kwargs):
        if path.name in {'usage.json', 'native-intent.json', 'native-outcome.json'}:
            reads.append(path)
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(c, 'read', counted)
    monkeypatch.setattr(native, 'read', counted)
    memo = {}
    first = ui_saved_status._usage(root, [1], memo)
    assert first['observed_tokens'] == first['accounted_tokens'] == expected['observed_tokens'] == 2410823
    assert first['unknown_steps'] == expected['unknown_steps'] == [1]
    assert len(reads) == 6  # one read per saved meter, intent and outcome
    reads.clear()
    assert ui_saved_status._usage(root, [1], memo) == first
    assert reads == []
    assert all(path.read_bytes() == data for path, data in originals.items())

    # Simulate a saved-record replacement: stat identity invalidates only the
    # affected memo entries, while the effective counter still matches control.
    updated = {key: TERMINAL[key] + extra for key, extra in
               zip(TERMINAL, (100, 20, 2))}
    updated_delta = {key: updated[key] - BASELINE[key] for key in BASELINE}
    metadata = dict(metadata, tokens=updated, live_tokens=updated_delta)
    outcome = original_read(resumed / 'native-outcome.json')
    outcome['metering'] = metadata
    c.write(resumed / 'usage.json', updated)
    c.write(resumed / 'native-outcome.json', outcome)
    c.write(resumed / 'usage-observation.json', metadata)
    changed_expected = control_usage(root)
    reads.clear()
    changed = ui_saved_status._usage(root, [1], memo)
    assert changed['observed_tokens'] == changed['accounted_tokens'] == changed_expected['observed_tokens'] == 2410925
    assert changed['unknown_steps'] == [1]
    assert set(reads) == {resumed / 'usage.json', resumed / 'native-outcome.json'}
    reads.clear()
    assert ui_saved_status._usage(root, [1], memo) == changed and reads == []
    assert all(path.read_bytes() == data for path, data in originals.items() if path.parent == original)


def test_role_share_uses_same_effective_consumption_without_false_optional_a_refusal(tmp_path, monkeypatch):
    from scidiscovery.research_loop import controller
    from test_node_budget import policy
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Account retained H work before an optional A analysis.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'], max_tokens=8000000,
        max_seconds=120, max_steps=6, model='fixture-no-model', reasoning_effort='high',
        node_budget_mode='soft', node_budget_policy=policy(tokens=5000000),
        role_budget_policy=role_share.DEFAULT)
    job = c.build_job(root, 1, 'H', [])
    original = execution.origin(root, 1)
    resumed, _ = old_record(original)
    paths = [original, resumed]
    limits = budget.node_limits(root, job)
    previous = 0
    for number, (record, meter) in enumerate(zip(paths, [BASELINE, TERMINAL])):
        intent = c.read(record / 'native-intent.json') if number else {}
        intent.update(task_ref=c.record_ref(root / 'jobs/001.json'),
            budget_slice=dict(role='H', limits=limits, previous_tokens=previous, previous_seconds=number))
        c.write(record / 'native-intent.json', intent)
        c.write(record / 'usage.json', meter)
        outcome = c.read(record / 'native-outcome.json') if number else dict(metering=dict(coverage='unknown'))
        previous += meter['input_tokens'] + meter['output_tokens']
        outcome['budget_slice'] = dict(role='H', limits=limits, usage_observed=True, dispatches=number+1,
            advance_seconds=number+1, observed_tokens=previous, **meter)
        c.write(record / 'native-outcome.json', outcome)
    monkeypatch.setattr(execution, 'records', lambda selected_root, step: paths)
    originals = {path: path.read_bytes() for record in paths for path in record.iterdir() if path.is_file()}
    shared = native.usage(root)
    role = role_share.usage(root, 1)
    assert shared['unknown_steps'] == [1]
    assert role['H'] == role['thinking'] == shared['observed_tokens'] == 2410823
    assert budget._attempt_consumption(root, job, paths) == (2410823, 2)
    assert role['thinking'] < role_share.DEFAULT['max_thinking_share'] * 8000000 < previous
    assert role_share.reason(root, 1, job, dict(next_action='analyze'), 'A') is None
    # Missing later billing remains missing in each consumer, and never adds an
    # invented zero meter or changes the retained known lower bound.
    pending = original / 'continuations/002'
    pending.mkdir()
    c.write(pending / 'native-intent.json', dict(attempt=2))
    paths.append(pending)
    assert role_share.usage(root, 1)['unknown_steps'] == [1]
    assert role_share.usage(root, 1)['thinking'] == 2410823
    assert native.usage(root)['unknown_steps'] == [1]
    assert all(path.read_bytes() == data for path, data in originals.items())


@pytest.mark.parametrize('prefix', ['effective', 'recorded'])
def test_original_node_reports_stay_valid_while_effective_consumption_matches_shared(tmp_path, monkeypatch, prefix):
    from test_node_budget import instance, policy
    root = instance(tmp_path, limits=policy(tokens=5000000), max_tokens=5000000)
    job = c.build_job(root, 1, 'H', [])
    original = execution.origin(root, 1)
    resumed, _ = old_record(original)
    paths = [original, resumed]
    limits = budget.node_limits(root, job)
    previous = 0
    for number, (record, meter) in enumerate(zip(paths, [BASELINE, TERMINAL])):
        intent = c.read(record / 'native-intent.json') if number else {}
        intent.update(task_ref=c.record_ref(root / 'jobs/001.json'),
            budget_slice=dict(role='H', limits=limits, previous_tokens=previous, previous_seconds=number))
        if number:
            intent.update(root_intent_ref=c.record_ref(original / 'native-intent.json'),
                previous={'native-intent.json': c.record_ref(paths[number-1] / 'native-intent.json')})
        c.write(record / 'native-intent.json', intent)
        c.write(record / 'usage.json', meter)
        outcome = c.read(record / 'native-outcome.json') if number else dict(metering=dict(coverage='terminal'))
        previous += meter['input_tokens'] + meter['output_tokens']
        outcome['budget_slice'] = dict(role='H', limits=limits, usage_observed=True, dispatches=number+1,
            advance_seconds=number+1, observed_tokens=previous, **meter)
        c.write(record / 'native-outcome.json', outcome)
    monkeypatch.setattr(execution, 'records', lambda selected_root, step: paths)
    originals = {path: path.read_bytes() for record in paths for path in record.iterdir() if path.is_file()}
    assert budget._attempt_consumption(root, job, paths) == (2410823, 2)
    assert native.usage(root)['observed_tokens'] == 2410823

    # A third attempt can have been accepted before or after this correction.
    # Both prefixes must be proven by the same retained original reports; no
    # arbitrary reduction of consumption or time is accepted.
    current = original / 'continuations/002'
    current.mkdir()
    selected_prefix = 2410823 if prefix == 'effective' else previous
    clock = dict(max_tokens=5000000, max_seconds=120, advance_seconds=13.)
    intent = dict(attempt=2, task_ref=c.record_ref(root / 'jobs/001.json'),
        root_intent_ref=c.record_ref(original / 'native-intent.json'),
        previous={'native-intent.json': c.record_ref(resumed / 'native-intent.json')},
        budget_slice=dict(role='H', task_ref=c.record_ref(root / 'jobs/001.json'),
            limits=limits, shared_limits=dict(max_tokens=5000000, max_seconds=120),
            advance_seconds=10., previous_tokens=selected_prefix, previous_seconds=2.))
    c.write(current / 'native-intent.json', intent)
    paths.append(current)
    assert budget._node_duration(root, job, intent, clock) == 5.
    assert budget._node_duration(root, job, intent, dict(clock, advance_seconds=14.)) == 6.
    assert budget.used_steps(root) == 3
    for field, invalid in [('previous_tokens', 2410822), ('previous_seconds', 1.)]:
        changed = copy.deepcopy(intent)
        changed['budget_slice'][field] = invalid
        c.write(current / 'native-intent.json', changed)
        with pytest.raises(ValueError, match='cannot refund original consumption'):
            budget._node_duration(root, job, changed, clock)
    c.write(current / 'native-intent.json', intent)
    next_meter = dict(input_tokens=10, cached_input_tokens=2, output_tokens=3)
    c.write(current / 'usage.json', next_meter)
    c.write(current / 'native-outcome.json', dict(metering=dict(coverage='terminal'),
        budget_slice=dict(role='H', limits=limits, usage_observed=True, dispatches=3,
            advance_seconds=5., observed_tokens=selected_prefix+13, **next_meter)))
    assert budget._attempt_consumption(root, job, paths) == (2410836, 5.)
    assert native.usage(root)['observed_tokens'] == native.usage(root)['accounted_tokens'] == 2410836
    assert budget.used_steps(root) == 3
    assert all(path.read_bytes() == data for path, data in originals.items())


@pytest.mark.parametrize('mutation', ['fresh', 'thread', 'missing_baseline', 'unknown_live',
    'missing_live', 'different_total', 'already_delta', 'matched', 'protocol'])
def test_uncertain_or_already_local_records_do_not_receive_a_second_subtraction(tmp_path, mutation):
    record, metadata = old_record(tmp_path)
    intent = c.read(record / 'native-intent.json')
    value = copy.deepcopy(TERMINAL)
    if mutation == 'fresh':intent['context_mode'] = 'new_context'
    if mutation == 'thread':intent['session']['thread_id'] = 'other-thread'
    if mutation == 'missing_baseline':metadata.pop('baseline_tokens')
    if mutation == 'unknown_live':metadata['live_coverage'] = 'unknown'
    if mutation == 'missing_live':metadata.pop('live_tokens')
    if mutation == 'different_total':value['output_tokens'] += 1
    if mutation == 'already_delta':value = copy.deepcopy(DELTA); metadata['tokens'] = value
    if mutation == 'matched':metadata['reconciliation'] = 'matched'
    if mutation == 'protocol':metadata['source'] = 'protocol.completed'
    c.write(record / 'native-intent.json', intent)
    c.write(record / 'usage.json', value)
    outcome = c.read(record / 'native-outcome.json')
    outcome['metering'] = metadata
    c.write(record / 'native-outcome.json', outcome)
    assert native.attempt_usage(record) == value
