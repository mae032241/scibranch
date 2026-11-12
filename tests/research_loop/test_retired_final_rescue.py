"""Retired FINAL rescue cannot bypass ordinary routing or consumption checks."""
import pytest

from scidiscovery.research_loop import accepted_budget, budget, cli, contracts as c, continuation, execution, native, profiles, runtime
from test_node_budget import instance
from test_resume_usage_accounting import BASELINE, DELTA, TERMINAL


@pytest.mark.parametrize('command', ['reconcile-final', 'native-startup-successor', 'native-startup-collect'])
def test_retired_cli_command_is_rejected_before_opening_instance(tmp_path, command):
    root = tmp_path / 'absent-instance'
    with pytest.raises(SystemExit) as error:
        cli.main([command, '--root', str(root), '--report', str(tmp_path / 'absent-report')])
    assert error.value.code == 2
    assert not root.exists()


@pytest.mark.parametrize('ref', [None, {'path': '/absent-retired-report'}])
def test_saved_rescue_event_is_explicitly_unsupported(tmp_path, ref):
    root = instance(tmp_path)
    cursor = dict(role='H', parents=[], instruction='', evidence_return=None,
        correction_request=None, author_correction=None, source_refs=[])
    begin = budget.begin_closeout(root, 0, 1, 'token_limit', final_only=True, resume_cursor=cursor)
    settings = c.settings(root)
    events, parent = budget._events(root)
    # Reproduce the obsolete persisted event without opening any rescue report.
    rescued = budget._append(root, settings, events, parent, kind='closeout_resume',
        source_final=budget._event_ref(root, begin), first_step=2, reconciliation_ref=ref)
    originals = {path: path.read_bytes() for path in
        [root / 'settings.json', root / 'control-ledger' / f'{begin["seq"]:06d}.json',
         root / 'control-ledger' / f'{rescued["seq"]:06d}.json']}
    with pytest.raises(ValueError, match='rescue events are no longer supported'):
        budget._validate_resume_event(root, rescued, events)
    assert all(path.read_bytes() == value for path, value in originals.items())


def test_reentry_validates_one_persisted_attempt_prefix(tmp_path, monkeypatch):
    root = instance(tmp_path, max_tokens=5_000_000)
    job = c.build_job(root, 1, 'H', [])
    first = execution.origin(root, 1)
    prior = first / 'continuations/001'
    prior.mkdir(parents=True)
    task_ref = c.record_ref(root / 'jobs/001.json')
    limits = budget.node_limits(root, job)
    stamp = budget.clock_stamp()
    previous = 0
    for number, (record, meter) in enumerate(((first, BASELINE), (prior, DELTA))):
        anchor = dict(role='H', limits=limits, boot_id=stamp['boot_id'], advance_seconds=float(number),
            previous_tokens=previous, previous_seconds=float(number))
        c.write(record / 'native-intent.json', dict(step=1, task_ref=task_ref,
            **(dict(attempt=number, session=dict(home='/retained-home')) if number else {}), budget_slice=anchor))
        accepted_budget.accept_native(root, 1, record, c.read(record/'native-intent.json'))
        c.write(record / 'usage.json', meter)
        previous += meter['input_tokens'] + meter['output_tokens']
        report = dict(role='H', limits=limits, dispatches=number+1, advance_seconds=float(number+1),
            usage_observed=True, observed_tokens=previous, **meter)
        if number == 0:
            c.write(record / 'native-outcome.json', dict(metering=dict(coverage='terminal'), budget_slice=report))
            accepted_budget.accept_terminal(root, 1, record, meter=meter, unknown=False, metering=dict(coverage='terminal'))
    refs = {}
    for name in ('runner-process.json', 'native-process.json'):
        c.write(prior / name, dict(task_ref=c.record_ref(prior / 'native-intent.json'), boot_id='old-boot',
            pid=999999999, start_ticks=1, pgid=999999999, sid=999999999, ppid=999999998))
        refs[name] = c.record_ref(prior / name)
    marker = dict(task_ref=task_ref, intent_ref=c.record_ref(prior / 'native-intent.json'),
        advance_seconds=3., stamp=dict(boot_id='new-boot'), budget_slice=report, processes=refs)
    c.write(prior / 'interruption.json', marker)
    selected = dict(home='/retained-home', path='/retained-home/session.jsonl')
    monkeypatch.setattr(continuation, 'session', lambda *args, **kwargs: selected)
    anchor = dict(c.read(prior / 'native-intent.json')['budget_slice'], boot_id='new-boot',
        advance_seconds=3., previous_tokens=previous, previous_seconds=2.)
    intent = dict(budget_slice=anchor, session=selected, interruption=c.record_ref(prior / 'interruption.json'))
    continuation.validate_reentry(root, job, intent, prior, saved=marker,
        original=c.read(prior/'native-intent.json'), settings=c.settings(root, job),
        source_session=selected, accepted_anchor=anchor)
    assert budget._attempt_consumption(root, job, [first, prior]) == (previous, 2.)
    assert native.usage(root)['observed_tokens'] == previous == TERMINAL['input_tokens'] + TERMINAL['output_tokens']
    originals = {path: path.read_bytes() for path in (first / 'usage.json', prior / 'usage.json')}
    # A saved high prefix cannot become a second accepted accounting convention.
    marker['budget_slice']['observed_tokens'] += BASELINE['input_tokens'] + BASELINE['output_tokens']
    c.write(prior / 'interruption.json', marker)
    intent['interruption'] = c.record_ref(prior / 'interruption.json')
    intent['budget_slice']['previous_tokens'] = marker['budget_slice']['observed_tokens']
    with pytest.raises(ValueError, match='cumulative node consumption changed'):
        budget._attempt_consumption(root, job, [first, prior])
    assert all(path.read_bytes() == value for path, value in originals.items())


def test_saved_startup_successor_cannot_enter_supported_continuation_chain(tmp_path, monkeypatch):
    from scidiscovery.research_loop import native_usage
    from test_reboot_reentry import THREAD, interrupted_instance
    accept_node = budget.accept_node
    def accept_original(root, job):
        anchor = accept_node(root, job)
        record = execution.origin(root, job['step'])
        intent = dict(step=job['step'], task_ref=c.record_ref(root/'jobs/001.json'),
            budget_slice=anchor, **profiles.binding(c.settings(root, job), job))
        c.write(record/'native-intent.json', intent)
        accepted_budget.accept_native(root, job['step'], record, intent)
        return anchor
    # Publish the exact original while the reboot fixture still owns its
    # launch clock; its later identity consumer must observe this acceptance.
    with monkeypatch.context() as fixture:
        fixture.setattr(budget, 'accept_node', accept_original)
        root, originals = interrupted_instance(tmp_path, monkeypatch)
    # Retain hook metadata for the fixture's actual original transcript. The
    # supported reentry path validates this binding and the original thread event.
    record = execution.origin(root, 1)
    home = execution.home(root, 1)
    session = native_usage.stream_binding(home/'sessions/original.jsonl')
    session_binding = dict(source='native_metadata_hooks', thread_id=THREAD,
        home=str(home), workspace=str(c.workspace(root, 1)),
        path=session['path'], identity=session['identity'], offset=session['size_bytes'])
    observation = record/'usage-observation.json'
    c.write(observation, dict(coverage='unknown', session_binding=session_binding))
    originals[observation] = observation.read_bytes()
    monkeypatch.setattr(runtime, 'start', lambda *args: None)
    retained = continuation.reenter(root, 1)
    assert retained['continuation_kind'] == 'reentry'
    assert retained['session'] == session_binding
    current = execution.record(root, 1)
    assert execution.validate_records(root, 1) == [record, current]
    intent = c.read(current / 'native-intent.json')
    intent['continuation_kind'] = 'startup_successor'
    c.write(current / 'native-intent.json', intent)
    with pytest.raises(ValueError, match='kind differs from its frozen policy'):
        execution.validate_records(root, 1)
    assert all(path.read_bytes() == value for path, value in originals.items())
