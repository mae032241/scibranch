"""Reboots exclude downtime without rewriting closed original consumption."""
import json

import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, execution, ui_saved_status


@pytest.fixture
def clocked(tmp_path, monkeypatch, request):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain bounded work across a host reboot.')
    root = tmp_path / 'instance'
    if getattr(request, 'param', None) == 'elapsed':
        initialize = accepted_budget.initialize
        def legacy_birth(selected, settings, **facts):
            assert not (selected / accepted_budget.HEAD).exists()
            settings.pop('time_budget_basis')
            c.write(selected / 'settings.json', settings)
            return initialize(selected, settings, **facts)
        monkeypatch.setattr(accepted_budget, 'initialize', legacy_birth)
    controller.prepare(root, objective, public, ['fixture-never-launch'], max_tokens=1000,
        max_seconds=100, max_steps=4, model='fixture', reasoning_effort='high',
        node_budget_policy={role: dict(max_observed_tokens=100, max_seconds=30) for role in 'HWAE'})
    stamp = dict(at_unix=1000., at_boottime=100., boot_id='first-boot')
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(stamp))
    c.write(root / 'host-started.json', stamp)
    return root, stamp


def transition(root, phase):
    with controller._writer(root):
        budget.transition(root, phase, 'fixture transition')


def display_events(root):
    # This saved-display reducer consumes a complete supplied clock stream,
    # not AcceptedEvents' bounded latest-kind projection. Acquisition is here.
    return budget.audit_events(root)[0]


def pending_clock(root, monkeypatch):
    """Crash after durable clock publication, before accepted-head installation."""
    replace = accepted_budget._replace
    def fail_head(path, value):
        if path == root / accepted_budget.HEAD:
            raise OSError('fixture crash before accepted head installation')
        return replace(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(accepted_budget, '_replace', fail_head)
        with pytest.raises(OSError, match='fixture crash'):
            transition(root, 'advance')
    return root / 'control-ledger/000001.json'


def observation(root):
    """An existing non-clock event retains the last known old-boot uptime."""
    settings = c.settings(root)
    with budget._ledger_writer(root):
        events, parent = budget._events(root)
        return budget._append(root, settings, events, parent, kind='budget_amend',
            before=dict(max_tokens=1000, max_seconds=100),
            after=dict(max_tokens=1000, max_seconds=100), authorization_note='Keep the original ceilings')


@pytest.mark.parametrize('phase', ['advance', 'waiting'])
@pytest.mark.parametrize('new_unix', [500., 100000.])
def test_open_reboot_uses_recorded_old_uptime_and_new_uptime(clocked, phase, new_unix):
    root, stamp = clocked
    transition(root, phase)
    stamp.update(at_unix=1007., at_boottime=107.)
    observation(root)
    original_paths = [root / 'settings.json', root / 'host-started.json',
        root / 'control-ledger/000001.json', root / 'control-ledger/000002.json']
    originals = {path: path.read_bytes() for path in original_paths}
    stamp.update(at_unix=new_unix, at_boottime=3., boot_id='second-boot')
    value = budget.snapshot(root)
    consumed = 10 if phase == 'advance' else 0  # Waiting is not consumed.
    assert value['consumed_seconds'] == consumed
    assert value['seconds_remaining'] == 100 - consumed
    assert value['unmeasured_shutdown_tail'] is True
    events = display_events(root)
    shown = ui_saved_status._budget(root, c.settings(root), events)
    assert shown['consumed_seconds'] == value['consumed_seconds']
    assert shown['unmeasured_shutdown_tail'] is True
    assert all(path.read_bytes() == data for path, data in originals.items())
    transition(root, 'paused')
    assert c.read(root / 'control-ledger/000003.json')['uptime_accounting'] is True
    stamp.update(at_unix=new_unix + 500, at_boottime=503.)
    assert budget.snapshot(root)['consumed_seconds'] == consumed
    assert budget.status_snapshot(root)['consumed_seconds'] == consumed
    events = display_events(root)
    assert ui_saved_status._budget(root, c.settings(root), events)['consumed_seconds'] == consumed
    assert all(path.read_bytes() == data for path, data in originals.items())


def test_multiple_reboots_retain_each_recorded_uptime_segment(clocked):
    root, stamp = clocked
    transition(root, 'advance')
    stamp.update(at_unix=1007., at_boottime=107.)
    observation(root)
    stamp.update(at_unix=5000., at_boottime=3., boot_id='second-boot')
    observation(root)
    stamp.update(at_unix=5004., at_boottime=7.)
    observation(root)
    stamp.update(at_unix=9000., at_boottime=2., boot_id='third-boot')
    assert budget.snapshot(root)['consumed_seconds'] == 16
    transition(root, 'paused')
    assert budget.snapshot(root)['consumed_seconds'] == 16
    transition(root, 'advance')
    stamp.update(at_unix=8500., at_boottime=6.)  # UTC correction does not refund uptime.
    assert budget.snapshot(root)['consumed_seconds'] == 20
    events = display_events(root)
    assert ui_saved_status._budget(root, c.settings(root), events)['consumed_seconds'] == 20


def test_unmarked_closed_cross_boot_consumption_keeps_its_original_charge(clocked):
    root, stamp = clocked
    settings = c.settings(root)
    for seq, event in enumerate([
        dict(at_unix=1000., at_boottime=100., boot_id='first-boot', phase='advance'),
        dict(at_unix=1020., at_boottime=2., boot_id='second-boot', phase='paused'),
    ], 1):
        path = root / 'control-ledger' / f'{seq:06d}.json'
        state = accepted_budget.read(root)
        # Supply a durable historical writer tail with its exact accepted
        # predecessor. The cold owner accepts it; no accepted head is reset.
        c.write(path, dict(seq=seq, kind='clock', parent_ref=state.parent,
            settings_ref=state['settings_ref'], reason='historical original',
            head_updates={}, index_updates={}, control_files={}, **event))
        accepted_budget.recover(root, accepted_budget.read(root))
    paths = [root / 'control-ledger/000001.json', root / 'control-ledger/000002.json']
    originals = {path: path.read_bytes() for path in paths}
    stamp.update(at_unix=9000., at_boottime=3., boot_id='third-boot')
    assert budget.snapshot(root)['consumed_seconds'] == 20
    events = display_events(root)
    assert ui_saved_status._budget(root, settings, events)['consumed_seconds'] == 20
    transition(root, 'advance')
    stamp.update(at_unix=9004., at_boottime=7.)
    assert budget.snapshot(root)['consumed_seconds'] == 24
    assert budget.snapshot(root)['max_seconds'] == settings['max_seconds']
    assert all(path.read_bytes() == data for path, data in originals.items())


def test_uptime_marker_is_validated(clocked, monkeypatch):
    root, _ = clocked
    path = pending_clock(root, monkeypatch)
    event = c.read(path)
    c.write(path, dict(event, uptime_accounting=1))
    # The pending original is acquired by the cold accepted owner, not ignored
    # behind an installed head. Its observed counter requires literal True.
    with pytest.raises(ValueError, match='advancement observation'):
        budget.snapshot(root)
    with pytest.raises(ValueError, match='uptime accounting marker'):
        budget.audit_events(root)


def test_cold_owner_rejects_uptime_marker_without_observed_counter(clocked, monkeypatch):
    root, _ = clocked
    path = pending_clock(root, monkeypatch)
    event = c.read(path)
    event.pop('observed_advance_seconds')
    c.write(path, dict(event, uptime_accounting=1))
    # The marker's own type must be checked even without the optional counter.
    # Historical auditing is not a substitute for the cold admission owner.
    with pytest.raises(ValueError, match='uptime accounting marker'):
        budget.snapshot(root)


@pytest.mark.parametrize('clocked', ['elapsed'], indirect=True)
def test_legacy_elapsed_does_not_adopt_uptime_accounting(clocked):
    root, stamp = clocked
    settings = c.settings(root)
    transition(root, 'advance')
    assert 'uptime_accounting' not in c.read(root / 'control-ledger/000001.json')
    stamp.update(at_unix=1080., at_boottime=3., boot_id='second-boot')
    assert budget.snapshot(root)['consumed_seconds'] == 80
    events = display_events(root)
    assert ui_saved_status._budget(root, settings, events)['consumed_seconds'] == 80


def save_intent(root, job, anchor, *, prepared_at=None):
    record = c.host_record(c.workspace(root, job['step']))
    intent = dict(step=job['step'], task_ref=c.record_ref(root / 'jobs' / f'{job["step"]:03d}.json'),
        budget_slice=anchor)
    if prepared_at is not None:
        intent['prepared_at'] = prepared_at
    accepted_budget.accept_native(root, job['step'], record, intent)
    c.write(record / 'usage.json', dict(input_tokens=0, cached_input_tokens=0, output_tokens=0))
    return intent


def retain_prior_boot(root, stamp, job, seconds):
    """Retain actual work before accepting a floor on a clock-event-free boot."""
    stamp.update(at_unix=1000. + seconds, at_boottime=100. + seconds)
    observation(root)
    stamp.update(at_boottime=0., boot_id='unobserved-second-boot')
    limits = budget.snapshot(root)
    assert limits['advance_seconds'] == seconds
    return dict(task_ref=c.record_ref(root / 'jobs/001.json'), role=job['role'],
        boot_id=stamp['boot_id'], advance_seconds=limits['advance_seconds'],
        limits=budget.node_limits(root, job),
        shared_limits={key: limits[key] for key in ('max_tokens', 'max_seconds')})


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('recorded_tail', [False, True])
def test_saved_node_anchor_survives_reboot_through_admission_and_reports(clocked, monkeypatch, legacy, recorded_tail):
    root, stamp = clocked
    transition(root, 'advance')
    job = c.build_job(root, 1, 'H', [])
    stamp.update(at_unix=1020., at_boottime=120.)
    with monkeypatch.context() as patch:
        if legacy:
            patch.setattr(budget, '_observe_node_clock', lambda selected: (budget.snapshot(selected), dict(stamp)))
        anchor = budget.accept_node(root, job)
    intent = save_intent(root, job, anchor)
    assert anchor['advance_seconds'] == 20
    if recorded_tail:
        stamp.update(at_unix=1030., at_boottime=130.)
        observation(root)
    paths = [root / 'settings.json', c.host_record(c.workspace(root, 1)) / 'native-intent.json']
    originals = {path: path.read_bytes() for path in paths}
    stamp.update(at_unix=5000., at_boottime=3., boot_id='second-boot')
    limits = budget.snapshot(root)
    expected = 33 if recorded_tail else 23
    assert limits['advance_seconds'] == expected
    assert limits['consumed_seconds'] == expected
    assert budget.node_admission(root, job, ignore_step=1, intent=intent) is None
    meter = dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)
    assert budget.node_report(root, job, intent, meter)['advance_seconds'] == expected - 20
    assert budget.stop_node(root, job, intent, limits, meter) is None
    events = display_events(root)
    assert ui_saved_status._budget(root, c.settings(root), events)['consumed_seconds'] == expected
    assert all(path.read_bytes() == data for path, data in originals.items())


def test_unobserved_old_boot_unix_anchor_is_retained_before_current_uptime(clocked):
    root, stamp = clocked
    transition(root, 'advance')
    job = c.build_job(root, 1, 'H', [])
    anchor = retain_prior_boot(root, stamp, job, 1000.)
    intent = save_intent(root, job, anchor)
    stamp.update(at_unix=9000., at_boottime=2., boot_id='third-boot')
    limits = budget.snapshot(root)
    assert limits['consumed_seconds'] == 1002
    assert budget._node_duration(root, job, intent, limits) == 2
    assert budget.node_admission(root, job, ignore_step=1, intent=intent) == 'node_budget_unavailable_seconds'
    events = display_events(root)
    assert ui_saved_status._budget(root, c.settings(root), events)['consumed_seconds'] == 1002
    transition(root, 'paused')
    events = display_events(root)
    assert ui_saved_status._budget(root, c.settings(root), events)['consumed_seconds'] == 1002
    transition(root, 'advance')
    observation(root)
    stamp.update(at_unix=12000., at_boottime=3., boot_id='fourth-boot')
    assert budget.snapshot(root)['consumed_seconds'] == 1005
    assert budget._node_duration(root, job, intent, budget.snapshot(root)) == 5
    events = display_events(root)
    assert ui_saved_status._budget(root, c.settings(root), events)['consumed_seconds'] == 1005
    transition(root, 'paused')
    events = display_events(root)
    assert ui_saved_status._budget(root, c.settings(root), events)['consumed_seconds'] == 1005


def test_reboot_node_duration_retains_original_prior_consumption(clocked):
    root, stamp = clocked
    transition(root, 'advance')
    job = c.build_job(root, 1, 'H', [])
    stamp.update(at_unix=1020., at_boottime=120.)
    anchor = budget.accept_node(root, job)
    intent = save_intent(root, job, anchor)
    first = c.host_record(c.workspace(root, 1))
    stamp.update(at_unix=1027., at_boottime=127.)
    meter = dict(input_tokens=5, cached_input_tokens=0, output_tokens=0)
    report = dict(budget.node_report(root, job, intent, meter), usage_observed=True)
    c.write(first / 'usage.json', meter)
    c.write(first / 'native-outcome.json', dict(returncode=0, stop_reason=None, budget_slice=report))
    accepted_budget.accept_terminal(root, 1, first, meter=meter, unknown=False)
    continued_anchor = budget.accept_continuation(root, job, [first])
    assert continued_anchor['previous_seconds'] == 7 and continued_anchor['previous_tokens'] == 5
    continued = dict(intent, attempt=1, continuation_kind='repair',
        root_intent_ref=c.record_ref(first / 'native-intent.json'), previous=execution.proof(first),
        budget_slice=continued_anchor)
    state = accepted_budget.accept_native(root, 1, first / 'continuations/001', continued)
    # Re-observing the completed predecessor cannot charge its meter twice or
    # replace the continuation's accepted clock/floor.
    repeated = accepted_budget.accept_terminal(root, 1, first, meter=meter, unknown=False)
    assert repeated['seq'] == state['seq']
    assert repeated['current_native'] == state['current_native']
    assert repeated['usage']['input_tokens'] == 5
    stamp.update(at_unix=5000., at_boottime=3., boot_id='second-boot')
    assert budget._node_duration(root, job, continued, budget.snapshot(root)) == 10


@pytest.mark.parametrize('payload', ['{unreadable original', '[]', 'null'])
def test_unreadable_native_floor_keeps_ui_limits_with_unknown_time(clocked, payload):
    root, stamp = clocked
    transition(root, 'advance')
    job = c.build_job(root, 1, 'H', [])
    stamp.update(at_unix=1020., at_boottime=120.)
    anchor = budget.accept_node(root, job)
    save_intent(root, job, anchor)
    original = c.host_record(c.workspace(root, 1)) / 'native-intent.json'
    original.chmod(0o600)  # Deliberately damage an accepted immutable original.
    original.write_text(payload)
    stamp.update(at_unix=5000., at_boottime=3., boot_id='second-boot')
    events = display_events(root)
    shown = ui_saved_status._budget(root, c.settings(root), events)
    assert shown['max_tokens'] == 1000 and shown['max_seconds'] == 100
    assert shown['consumed_seconds'] is None and shown['seconds_remaining'] is None
    assert shown['time_status'] == 'unknown'
    # Budget consumers retain the accepted floor. Reopening and rejecting
    # damaged intent bytes belongs to cold execution acquisition.
    assert budget.snapshot(root)['consumed_seconds'] == 23
    with pytest.raises(ValueError):
        list(execution.accepted_intents(root, job['step']))


@pytest.mark.parametrize('phase, expected', [('paused', 4003), ('advance', 4005)])
def test_closed_observation_survives_orphan_anchor_utc_reordering(clocked, phase, expected):
    root, stamp = clocked
    transition(root, 'advance')
    job = c.build_job(root, 1, 'H', [])
    anchor = retain_prior_boot(root, stamp, job, 4000.)
    intent = save_intent(root, job, anchor, prepared_at=5000.)
    stamp.update(at_unix=3000., at_boottime=3., boot_id='third-boot')
    assert budget.snapshot(root)['consumed_seconds'] == 4003
    if phase == 'paused':
        transition(root, 'paused')
    else:
        budget._observe_node_clock(root)  # Same phase persists the observed counter.
    events = display_events(root)
    assert events[-1]['observed_advance_seconds'] == 4003
    assert ui_saved_status._budget(root, c.settings(root), events)['consumed_seconds'] == 4003
    stamp.update(at_unix=6000., at_boottime=2., boot_id='fourth-boot')
    value = budget.snapshot(root)
    assert value['consumed_seconds'] == expected
    events = display_events(root)
    assert ui_saved_status._budget(root, c.settings(root), events)['consumed_seconds'] == expected
    assert budget._node_duration(root, job, intent, value) == expected - 4000


def test_nonclock_observation_survives_orphan_anchor_utc_reordering(clocked):
    root, stamp = clocked
    transition(root, 'advance')
    job = c.build_job(root, 1, 'H', [])
    anchor = retain_prior_boot(root, stamp, job, 4000.)
    intent = save_intent(root, job, anchor, prepared_at=5000.)
    stamp.update(at_unix=3000., at_boottime=3., boot_id='third-boot')
    assert budget.snapshot(root)['consumed_seconds'] == 4003
    observed = observation(root)
    assert observed['kind'] == 'budget_amend' and observed['observed_advance_seconds'] == 4003
    assert budget.snapshot(root)['consumed_seconds'] == 4003
    stamp.update(at_unix=6000., at_boottime=2., boot_id='fourth-boot')
    value = budget.snapshot(root)
    assert value['consumed_seconds'] == 4005
    assert budget._node_duration(root, job, intent, value) == 5
    events = display_events(root)
    assert ui_saved_status._budget(root, c.settings(root), events)['consumed_seconds'] == 4005
    transition(root, 'paused')
    assert budget.snapshot(root)['consumed_seconds'] == 4005
    events = display_events(root)
    assert ui_saved_status._budget(root, c.settings(root), events)['consumed_seconds'] == 4005


@pytest.mark.parametrize('invalid', [True, -1, float('inf')])
def test_recorded_advancement_observation_is_validated(clocked, monkeypatch, invalid):
    root, _ = clocked
    path = pending_clock(root, monkeypatch)
    event = c.read(path)
    path.chmod(0o600)  # Deliberate tamper of an immutable crash-tail original.
    path.write_text(json.dumps(dict(event, observed_advance_seconds=invalid)))
    with pytest.raises(ValueError, match='advancement observation'):
        budget.snapshot(root)
    with pytest.raises(ValueError, match='recorded advancement observation'):
        budget.audit_events(root)
    shown = ui_saved_status._budget(root, c.settings(root), [c.read(path)])
    assert shown['consumed_seconds'] is None and shown['seconds_remaining'] is None


def test_final_grant_replays_observed_counter_and_reuses_original_acceptance(clocked):
    root, stamp = clocked
    transition(root, 'advance')
    stamp.update(at_unix=1007., at_boottime=107.)
    budget.begin_closeout(root, 0, 1, 'Retain the original final scientific return', final_only=True)
    job = c.build_job(root, 1, 'A', [], final_report=dict(source_cutoff=0, stopped_original=None))
    anchor = budget.accept_node(root, job)
    events, _ = budget.audit_events(root)
    grants = [event for event in events if event['kind'] == 'final_a_return']
    assert len(grants) == 1
    grant = grants[0]
    assert grant['uptime_accounting'] is True
    assert grant['observed_advance_seconds'] == 7
    assert anchor['final_return']['seq'] == grant['seq']
    accepted = budget.final_a_return(root, job,
        dict(step=job['step'], task_ref=anchor['task_ref'], budget_slice=anchor))
    assert accepted['seq'] == grant['seq']
    assert accepted['ref'] == c.record_ref(root / 'control-ledger' / f'{grant["seq"]:06d}.json')
    assert all(accepted[key] == grant[key] for key in accepted if key != 'ref')
    path = root / 'control-ledger' / f'{grant["seq"]:06d}.json'
    original = path.read_bytes()
    stamp.update(at_unix=1010., at_boottime=110.)
    assert budget.accept_node(root, job) == anchor
    replay, _ = budget.audit_events(root)
    assert len(replay) == len(events)
    assert sum(event['kind'] == 'final_a_return' for event in replay) == 1
    assert path.read_bytes() == original


def test_prestart_amendment_does_not_precede_original_start_clock(clocked, monkeypatch):
    root, stamp = clocked
    (root / 'host-started.json').unlink()
    budget.amend(root, max_seconds=120,
        authorization_note='Retain this authorized ceiling before the first host start')
    event = c.read(root / 'control-ledger/000001.json')
    original = (root / 'control-ledger/000001.json').read_bytes()
    assert event['kind'] == 'budget_amend' and event['observed_advance_seconds'] == 0
    stamp.update(at_unix=1010., at_boottime=110.)
    c.write(root / 'host-started.json', stamp)
    assert budget.snapshot(root)['consumed_seconds'] == 0
    with monkeypatch.context() as patch:
        patch.setattr(budget, 'clock_stamp', lambda: pytest.fail('Initially paused status uses only saved facts'))
        assert budget.status_snapshot(root)['seconds_remaining'] == 120
        events = display_events(root)
        assert ui_saved_status._budget(root, c.settings(root), events)['seconds_remaining'] == 120
    stamp.update(at_unix=1012., at_boottime=112.)
    transition(root, 'advance')
    initial = budget.snapshot(root)
    assert initial['advance_seconds'] == 0 and initial['paused_seconds'] == 2
    assert initial['max_seconds'] == 120 and initial['seconds_remaining'] == 120
    stamp.update(at_unix=1015., at_boottime=115.)
    value = budget.snapshot(root)
    assert value['advance_seconds'] == 3 and value['consumed_seconds'] == 3
    assert value['seconds_remaining'] == 117
    events = display_events(root)
    assert ui_saved_status._budget(root, c.settings(root), events)['consumed_seconds'] == 3
    assert (root / 'control-ledger/000001.json').read_bytes() == original


def test_initial_paused_status_uses_post_start_amendment_without_live_clock(clocked, monkeypatch):
    root, stamp = clocked
    stamp.update(at_unix=1010., at_boottime=110.)
    budget.amend(root, max_seconds=120, authorization_note='Retain the ceiling after host start')
    paths = [root / 'host-started.json', root / 'control-ledger/000001.json']
    originals = {path: path.read_bytes() for path in paths}
    monkeypatch.setattr(budget, 'clock_stamp', lambda: pytest.fail('Initially paused status uses only saved facts'))
    assert budget.status_snapshot(root)['seconds_remaining'] == 120
    events = display_events(root)
    assert ui_saved_status._budget(root, c.settings(root), events)['seconds_remaining'] == 120
    assert all(path.read_bytes() == data for path, data in originals.items())
