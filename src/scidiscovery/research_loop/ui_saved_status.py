"""Display saved control records without invoking scientific admission readers."""
import copy
import math

from . import budget, contracts as c, instance_display as display, native_usage, runtime
from .ui_view import memo_lock


def _read(path, default=None):
    return c.read(path) if path.exists() else default


def _saved_read(path, memo):
    """UI-only immutable record memo; never used by control admission."""
    if memo is None:
        return _read(path)
    with memo_lock(memo):
        return _memo_read(path, memo)


def _memo_read(path, memo):
    key = str(path)
    try:
        info = path.stat()
    except FileNotFoundError:
        memo.pop(key, None)
        return None
    identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    saved = memo.get(key)
    if saved is not None and saved[0] == identity:
        return copy.deepcopy(saved[1])
    memo.pop(key, None)
    value = c.read(path)
    try:
        after = path.stat()
    except FileNotFoundError:
        return value
    if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        return value
    while len(memo) >= 1024:
        memo.pop(next(iter(memo)))
    memo[key] = (identity, copy.deepcopy(value))
    return value


def steps(root):
    return sorted(int(path.stem) for path in (root / 'jobs').glob('*.json')
        if path.stem.isascii() and path.stem.isdecimal() and path.stem == f'{int(path.stem):03d}')


def visible_steps(root):
    from . import fork_runtime, fork_sources
    inherited = ({row['step'] for row in fork_sources.validate_manifest(root)['prefix_entries']}
                 if fork_runtime.is_fork(root) else set())
    return sorted(set(steps(root)) | inherited)


def operations(root, *, memo=None):
    rows = []
    for path in (root / 'host-control/operations').glob('*.json'):
        with memo_lock(memo):
            cached = memo.get(str(path)) if memo is not None else None
            terminal = cached is not None and cached[1].get('status') in {'applied', 'completed', 'failed'}
            row = _saved_read(path, memo if terminal else None)
            if row is None:
                continue
            if memo is not None and row.get('status') in {'applied', 'completed', 'failed'} and not terminal:
                row = _saved_read(path, memo)
            if row is not None:
                rows.append(row)
    return sorted(rows, key=lambda row: row.get('sequence', 0))[-20:]


def attempts(root, step):
    record = root / 'native-evidence' / f'{step:03d}'
    return [record, *sorted(path for path in (record / 'continuations').glob('[0-9]*') if path.is_dir())]


def used_steps(root, numbers=None, events=None):
    from . import fork_runtime
    if fork_runtime.is_fork(root):
        return budget.used_steps(root)
    numbers = steps(root) if numbers is None else numbers
    events = events if events is not None else [c.read(path) for path in sorted((root / 'control-ledger').glob('*.json'))]
    continuations = sum(1 for step in numbers for record in attempts(root, step)[1:]
                        if (record / 'native-intent.json').exists())
    audits = sum((root / 'native-evidence' / f'{step:03d}' / 'candidate-audit/intent.json').exists() for step in numbers)
    audits += sum(1 for _ in (root / 'host-delivery/audit-recovery').glob('*/intent.json'))
    return max(numbers, default=0) + continuations + sum(event['kind'] == 'action_intent' for event in events) + audits


def final_return(root, events=None):
    """Select the saved active FINAL cycle; old v1 returns remain historical."""
    events = events if events is not None else [c.read(path) for path in sorted((root / 'control-ledger').glob('*.json'))]
    active = None
    for event in events:
        if event['kind'] == 'closeout_begin':
            active = event if event.get('mode') == 'final' else None
        elif event['kind'] == 'closeout_resume':
            active = None
    return next((event for event in reversed(events) if event['kind'] == 'final_a_return'
        and (event.get('version') == 1 or active is not None and event['step'] == active['first_step'])), None)


def _node(root, step, job):
    records = attempts(root, step)
    record = records[-1]
    outcome = _read(record / 'native-outcome.json', {})
    intent = _read(record / 'native-intent.json', {})
    if (records[0] / 'result-identity.json').exists():
        state = 'seal_present_unverified'
    elif outcome:
        state = ('waiting' if (record / 'execution-wait.json').exists() else
                 'native_not_submitted' if outcome.get('native_started') is False else 'native_outcome_unsealed')
    elif not intent:
        state = 'prepared'
    else:
        runner = _read(record / 'runner-process.json', {})
        try:
            state = 'running' if runner and runtime.process_matches(runner, runner.get('task_ref')) else 'unknown'
        except (OSError, ValueError, KeyError):
            state = 'unknown'
    return dict(step=step, role=job['role'], state=state, continuations=len(records) - 1,
        stop_reason=outcome.get('stop_reason'), returncode=outcome.get('returncode'),
        budget_slice=intent.get('budget_slice'), budget_report=outcome.get('budget_slice'))


def _usage(root, numbers, memo=None):
    totals = dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)
    unknown = []
    for step in numbers:
        for record in attempts(root, step):
            if not (record / 'native-intent.json').exists():
                continue
            sealed = (root / 'native-evidence' / f'{step:03d}' / 'result-identity.json').exists()
            terminal = sealed and (record / 'native-outcome.json').exists()
            cache = memo if terminal else None
            value = _saved_read(record / 'usage.json', cache)
            outcome = _saved_read(record / 'native-outcome.json', cache) or {}
            metadata = outcome.get('metering') or _saved_read(record / 'usage-observation.json', cache) or {}
            if value is None or metadata.get('coverage') == 'unknown':
                if step not in unknown:
                    unknown.append(step)
            if value is not None:
                for key in totals:
                    totals[key] += value.get(key, 0)
    observed = totals['input_tokens'] + totals['output_tokens']
    context = dict(status='unavailable')
    measurement_status = native_usage.measurement_status(None)
    if numbers:
        step = numbers[-1]
        record = attempts(root, step)[-1]
        metadata = _read(record / 'usage-observation.json', {})
        measurement_status = native_usage.measurement_status(metadata)
        if metadata.get('coverage') in {'live', 'terminal'}:
            context = native_usage.context(metadata.get('context'))
        context.update(step=step, attempt=0 if record.name == f'{step:03d}' and record.parent.name == 'native-evidence' else int(record.name))
    return dict(totals, observed_tokens=observed, accounted_tokens=observed, unknown_steps=unknown,
        **native_usage.breakdown(totals), context=context, measurement_status=measurement_status,
        reserved_tokens=0, reserved_unknown_steps=[], blocking_unknown_steps=[], accounting_basis='observed_lower_bound')


def _budget(root, settings, events):
    limits = {key: settings[key] for key in ('max_tokens', 'max_seconds', 'max_steps')}
    for event in events:
        if event['kind'] == 'budget_amend':
            limits.update(event['after'])
            if 'after_max_steps' in event:
                limits['max_steps'] = event['after_max_steps']
    basis = settings.get('time_budget_basis', 'elapsed')
    start = _read(root / 'host-started.json')
    now = None
    current_known = True
    phase = 'paused' if basis == 'active' else 'unclassified'
    def duration(before, after, *, exclude_downtime=False):
        # Saved display records are not admission-validated. Check readability
        # here, but let the control clock own interval and cross-boot rules.
        keys = ['at_unix']
        if 'at_boottime' in before:
            keys.append('at_boottime')
            if any(not isinstance(stamp.get('boot_id'), str) or not stamp['boot_id']
                    for stamp in (before, after)):
                return None
        elif basis != 'elapsed':
            return None
        for key in keys:
            values = (before.get(key), after.get(key))
            if any(type(value) not in (int, float) or not math.isfinite(value)
                    or key == 'at_boottime' and value < 0 for value in values):
                return None
        try:
            value = budget._duration(before, after, legacy_elapsed=basis == 'elapsed', exclude_downtime=exclude_downtime)
            return value if math.isfinite(value) and value >= 0 else None
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
    phase = next((event['phase'] for event in reversed(events) if event['kind'] == 'clock'), phase)
    if (start is not None or events) and (basis != 'active' or phase != 'paused'):
        now = budget.clock_stamp()
        current_known = not events or duration(events[-1], now) is not None
    try:
        floors = budget._accepted_advance_floors(root, start, events, now, basis=basis)
    except (OSError, ValueError, TypeError, KeyError):
        # An unreadable original prevents a usable time balance, but must not
        # hide unrelated saved results, token observations or configured caps.
        floors = None
    durations, phase, unmeasured_tail = budget._clock_durations(start, events, now, basis=basis,
        duration=duration, advance_floors=floors)
    if floors is None:
        durations['advance'] = None
    if (start is not None or events) and basis == 'active' and phase == 'paused':
        # The open paused tail does not change recorded active consumption.
        durations['paused'] = None
    missing_start = start is None and any(event['kind'] == 'clock' for event in events)
    if start is None:
        elapsed = None if missing_start else 0.
    elif now is None or not current_known or any(event.get('reanchor') for event in events):
        elapsed = None
    else:
        elapsed = duration(start, now)
        if basis == 'elapsed' and elapsed is not None:
            elapsed = duration(dict(at_unix=start['at_unix']), now)
    if basis == 'active':
        anchor = start.get('at_boottime') if start is not None else None
        anchored = start is None or (type(anchor) in (int, float) and math.isfinite(anchor)
                                    and anchor >= 0 and bool(start.get('boot_id')))
        consumed = (durations['advance'] + durations['waiting']
                    if not missing_start and anchored and current_known
                    and durations['advance'] is not None and durations['waiting'] is not None else None)
    else:
        consumed = elapsed
    return dict(limits, original_max_steps=settings['max_steps'], time_budget_basis=basis,
        advance_seconds=durations['advance'], waiting_seconds=durations['waiting'], paused_seconds=durations['paused'],
        unclassified_seconds=durations['unclassified'], elapsed_seconds=elapsed, consumed_seconds=consumed,
        seconds_remaining=max(0., limits['max_seconds'] - consumed) if consumed is not None else None,
        time_status='recorded' if consumed is not None else 'unknown', clock_phase=phase, ledger_events=len(events),
        **(dict(unmeasured_shutdown_tail=True) if unmeasured_tail else {}))


def _correction(root):
    pause = None
    for path in sorted((root / 'correction-ledger').glob('*.json')):
        event = c.read(path)
        if event['kind'] == 'trigger' and event['trigger'].get('level') == 'pause':
            pause = dict(event['trigger'], published=True)
        elif event['kind'] == 'ack' and pause and event.get('trigger_id') == pause['trigger_id']:
            pause = None
    return dict(current_pause=pause)


def closeout(root, events=None, numbers=None):
    events = events if events is not None else [c.read(path) for path in sorted((root / 'control-ledger').glob('*.json'))]
    numbers = steps(root) if numbers is None else numbers
    begin, actions, outcomes = None, [], {}
    for event in events:
        if event['kind'] == 'closeout_begin':
            begin = event
        elif event['kind'] == 'closeout_resume':
            begin = None
        elif event['kind'] == 'action_intent':
            actions.append(event)
        elif event['kind'] == 'action_outcome':
            outcomes[event['action_id']] = event['result']
    if begin is None:
        return None
    confirmed = [outcomes[row['action_id']] for row in actions if row['action_kind'] == 'confirm'
        and outcomes.get(row['action_id'], {}).get('status') == 'confirmed']
    active = [row for row in actions if row['seq'] > begin['seq']]
    last = active[-1] if active else None
    outcome = outcomes.get(last['action_id']) if last else None
    unresolved = any(row['action_id'] not in outcomes or outcomes[row['action_id']].get('status') == 'unknown'
        for row in actions if not any(item['operation_id'] == row['operation_id'] and item['seq'] > row['seq']
            and outcomes.get(item['action_id'], {}).get('status') == 'confirmed' and item['action_kind'] == 'confirm'
            for item in actions))
    pending = last is not None and (outcome is None or outcome.get('status') == 'unknown' or
        last['action_kind'] == 'submit' and outcome.get('status') == 'confirmed')
    completed = [step for step in numbers if step >= begin['first_step']
        and (root / 'native-evidence' / f'{step:03d}' / 'result-identity.json').exists()]
    final = final_return(root, events)
    final_steps = {final['step']} if final is not None else set()
    status = 'final_a_completed' if any(step in final_steps for step in completed) else 'closeout_pending'
    if begin['mode'] == 'export_only':
        status = 'exported_unsubmitted'
    elif unresolved:
        status = 'delivery_unknown'
    elif last:
        status = ('delivery_confirmation_pending' if last['action_kind'] == 'submit' and outcome.get('status') == 'confirmed'
            else 'delivery_' + outcome.get('status', 'unknown'))
    value = dict(status=status, last_confirmed=confirmed[-1] if confirmed else None,
        current_actual='unknown' if unresolved or pending else (confirmed[-1] if confirmed else None),
        goal_completion='not_asserted', used_steps=used_steps(root, numbers, events), source_cutoff=begin['source_cutoff'])
    if final is not None and final['step'] in completed:
        record = root / 'native-evidence' / f'{final["step"]:03d}'
        seal = c.read(record / 'result-identity.json')
        attempt = (seal.get('execution_ref') or {}).get('attempt', 0)
        if attempt:
            record = record / 'continuations' / f'{attempt:03d}'
        completion = _read(record / 'native-outcome.json', {}).get('final_a_completion')
        if completion is not None:
            value['final_overrun'] = completion['final_overrun']
    return value


def status(root, *, after=0, limit=None, memo=None):
    settings = display.settings(root)
    numbers = steps(root)
    paths = sorted((root / 'control-ledger').glob('*.json'))
    events = [_saved_read(path, memo if index < len(paths) - 1 else None) for index, path in enumerate(paths)]
    from . import fork_runtime, fork_sources, originals
    manifest = fork_sources.validate_manifest(root) if fork_runtime.is_fork(root) else None
    inherited = {row['step']: row for row in manifest['prefix_entries']} if manifest else {}
    visible = sorted(set(numbers) | set(inherited))
    selected = [step for step in visible if step > after]
    if limit is not None:
        selected = selected[:limit]
    nodes = [_node(root, step, c.read(root / 'jobs' / f'{step:03d}.json')) if step in numbers else
        dict(step=step, role=originals.source_job(originals.node(root, step))['role'],
             state='seal_present_unverified', inherited=True) for step in selected]
    last = selected[-1] if selected else after
    return dict(instance=str(root), goal_id=settings['goal_id'], objective=settings['objective'],
        paused=(root / 'pause.request').exists(), nodes=nodes, usage=_usage(root, numbers, memo),
        budget=_budget(root, settings, events), correction=_correction(root), closeout=closeout(root, events, numbers),
        closeout_preparation=None, goal_completion='not_asserted', after=after,
        used_steps=used_steps(root, numbers, events),
        **(dict(fork=dict(inherited_costs=manifest.get('inherited_costs',
            dict(status='unknown', reason='Exact cut costs are not reconstructed')))) if manifest else {}),
        next_after=last if any(step > last for step in visible) else None)
