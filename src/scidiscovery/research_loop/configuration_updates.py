"""Append-only operational settings, bound once at the start of a new node.

The initial settings identity and all original node bindings remain unchanged.
Saving queues a declaration; it does not launch, reopen FINAL, or reset a meter.
"""
from contextlib import contextmanager
import copy
import fcntl
import os
from pathlib import Path

from . import contracts as c


def _directory(root):
    return Path(root) / 'host-control/configuration'


@contextmanager
def _writer(root):
    """Independent configuration writer produces its original base once."""
    with _writer_from_base(root, base=c.settings(root)) as base:
        yield base


@contextmanager
def _writer_from_base(root, *, base):
    from .instance_owner import lease
    from .protocol import require_current_base
    root = Path(root).resolve()
    with lease(root):
        require_current_base(root, base=base)
        fd = os.open(root / 'configuration.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield base
        finally:
            os.close(fd)


def revisions(root, *, through=None):
    """Read and verify the contiguous immutable snapshot chain."""
    root = Path(root).resolve()
    paths = sorted((_directory(root) / 'revisions').glob('*.json'))
    if not paths or through == 0:
        return []
    original = c.settings(root)
    parent = dict(path='settings.json')
    rows = []
    for index, path in enumerate(paths, 1):
        if through is not None and index > through:
            break
        if path.name != f'{index:06d}.json':
            raise ValueError('Configuration revision sequence changed')
        identity = c.read(c.regular(_directory(root) / 'identities' / path.name))
        if identity != c.record_ref(c.regular(path)):
            raise ValueError('Configuration revision original changed')
        value = c.read(c.regular(path))
        if (value.get('version') != 1 or type(value.get('revision')) is not int
                or value['revision'] != index or value.get('predecessor') != parent
                or not isinstance(value.get('settings'), dict)):
            raise ValueError('Configuration revision identity changed')
        for key in ('goal_id', 'objective', 'public_root', 'global_materials', 'source_provenance',
                    'time_budget_basis', 'handoff_protocol', 'final_a_completion_policy', 'skills'):
            if value['settings'].get(key) != original.get(key):
                raise ValueError('Configuration revision changed immutable research identity')
        rows.append(value)
        parent = dict(path=path.relative_to(root).as_posix())
    return rows


def _reference(root, row):
    path = _directory(root) / 'revisions' / f'{row["revision"]:06d}.json'
    return dict(revision=row['revision'], path=path.relative_to(Path(root)).as_posix())


def _revision(root, ref, *, head):
    if not isinstance(ref, dict) or set(ref) != {'revision', 'path'} or type(ref.get('revision')) is not int or ref['revision'] < 1:
        raise ValueError('Exact configuration revision reference required')
    expected = f'host-control/configuration/revisions/{ref["revision"]:06d}.json'
    if ref['path'] != expected:
        raise ValueError('Configuration revision location changed')
    accepted = head.row(f'configuration/{ref["revision"]:06d}')
    if accepted is None or accepted['ref'] != dict(path=expected):
        raise ValueError('Configuration revision has not been accepted')
    value = c.read(c.regular(Path(root) / expected))
    if value.get('revision') != ref['revision']:
        raise ValueError('Configuration revision is missing or changed')
    return value


def _read_reference(root, ref, *, head):
    return copy.deepcopy(_revision(root, ref, head=head)['settings'])


def settings_path(root, job):
    """Exact policy original for recovery/export references."""
    for_job(root, job)
    return Path(root) / ((job.get('configuration_revision') or {}).get('path', 'settings.json'))


def for_job(root, job):
    """A retained node without a revision always uses its original base settings."""
    from . import accepted_budget
    if job.get('configuration_revision') is None:
        return policy_for_job(root, job, base=c.settings(root), head=None)
    head = accepted_budget.read(root)
    return policy_for_job(root, job, base=head.base, head=head)


def policy_for_job(root, job, *, base, head):
    """Consume a retained binding; base-policy nodes perform no further reads."""
    root = Path(root).resolve()
    ref = job.get('configuration_revision')
    if ref is None:
        return base
    if head is None:
        raise ValueError('Selected configuration requires its accepted head')
    return _read_reference(root, ref, head=head)


def for_step(root, step):
    root = Path(root).resolve()
    return for_job(root, c.read(c.regular(root / 'jobs' / f'{step:03d}.json')))


def _reservation(root, step, *, head):
    path = _directory(root) / 'bindings' / f'{step:06d}.json'
    row = head.row(f'configuration-binding/{step:06d}')
    if row is None:
        if path.exists():
            raise ValueError('Unaccepted configuration binding requires original-call reconciliation')
        return None
    if not path.exists() or c.read(c.regular(path)) != row:
        raise ValueError('Accepted configuration binding original changed')
    if set(row) != {'step', 'configuration_revision'} or row['step'] != step:
        raise ValueError('Configuration node binding changed')
    return row


def for_new_job(root, step):
    root = Path(root).resolve()
    path = root / 'jobs' / f'{step:03d}.json'
    if path.exists():
        return for_job(root, c.read(c.regular(path)))
    from . import accepted_budget
    head=accepted_budget.read(root)
    return policy_for_new_job(root, step, base=head.base, head=head)


def policy_for_new_job(root, step, *, base, head):
    """Select a new-node policy from facts produced by its current owner."""
    root = Path(root).resolve()
    path = root / 'jobs' / f'{step:03d}.json'
    if path.exists():
        return policy_for_job(root, c.read(c.regular(path)), base=base, head=head)
    reserved = _reservation(root, step, head=head)
    return (policy_for_job(root, reserved, base=base, head=head) if reserved is not None
        else current_policy(root, base=base, head=head))


def current(root):
    """Pure policy read: latest saved snapshot, without budget admission reads."""
    from . import accepted_budget
    head = accepted_budget.read(root)
    return current_policy(root, base=head.base, head=head)


def current_policy(root, *, base, head):
    """Select current policy from a head already produced by this operation."""
    selected = head['configuration']
    if selected is None:
        return base
    return _read_reference(root, dict(revision=selected['revision'], **selected['ref']), head=head)


def _current_revision(root, *, head):
    selected = head['configuration']
    return None if selected is None else _revision(root,
        dict(revision=selected['revision'], **selected['ref']), head=head)


def editable(root):
    """Editor projection: shared ledger ceilings plus explicit pending changes."""
    from . import budget
    root = Path(root)
    values = revisions(root)
    settings = copy.deepcopy(values[-1]['settings']) if values else c.settings(root)
    latest = len(values)
    bound = any((c.read(c.regular(path)).get('configuration_revision') or {}).get('revision', 0) == latest
                for path in (_directory(root) / 'bindings').glob('*.json'))
    pending_changes = values[-1].get('budget_changes', {}) if latest and not bound else {}
    ceilings = budget.ceilings(root)
    settings.update({key: ceilings[key] for key in ('max_tokens', 'max_seconds', 'max_steps') if key not in pending_changes})
    if settings.get('execution_resource_policy') is not None and (not latest or bound or not values[-1].get('resource_change')):
        accounting = budget.execution_accounting(root)
        if accounting['grant'] is not None:
            settings['execution_resource_policy']['allowance'] = dict(accounting['grant']['allowance'])
    return settings


def status(root):
    from . import research_config
    root = Path(root).resolve()
    values = revisions(root)
    latest = len(values)
    active_ref = None
    steps = set(int(path.stem) for path in (_directory(root) / 'bindings').glob('*.json'))
    steps.update(int(path.stem) for path in (root / 'jobs').glob('*.json'))
    for step in sorted(steps):
        path = root / 'jobs' / f'{step:03d}.json'
        if path.exists():
            job = c.read(c.regular(path))
            for_job(root, job)
            active_ref = job.get('configuration_revision')
        else:
            from . import accepted_budget
            head = accepted_budget.read(root)
            reserved = _reservation(root, step, head=head)
            policy_for_job(root, reserved, base=head.base, head=head)
            active_ref = reserved['configuration_revision']
    settings = editable(root)
    active = for_job(root, dict(configuration_revision=active_ref))
    return dict(status='configuration_inspected', revision=latest,
                active_revision=(active_ref or {}).get('revision', 0), pending=latest != (active_ref or {}).get('revision', 0),
                effective_from='next_new_node', configuration=research_config.to_document(root, settings),
                settings=settings, active_settings=active)


def _resource_authorization_revision(root, events):
    from . import budget
    from . import accepted_budget
    if isinstance(events, accepted_budget.AcceptedEvents):
        resource = events.state['resource']
        if resource is None:
            return None
        event = events.state.event(resource['grant_ref'])
        return budget._resource_ref(root, event) if (event.get('previous_grant') is not None
            or event.get('operation_id') is not None) else None
    # Initial seeding only materializes the frozen grant; later grants revise it.
    changes = [event for event in events if event['kind'] == 'resource_grant'
               and (event.get('previous_grant') is not None or event.get('operation_id') is not None)]
    return budget._resource_ref(root, changes[-1]) if changes else None


def _budget_amendment_arguments(changed):
    """An explicit removed ceiling differs from an unchanged amendment arg."""
    return {key: 'unlimited' if amount is None and key in {'max_tokens', 'max_seconds'} else amount
            for key, amount in changed.items()}


def submit(root, configuration, *, base=None, expected_revision=None,
           authorization_note=None):
    """Validate and queue a complete declaration using optimistic concurrency."""
    from . import research_config, budget, formal_review, execution_authority, fork_runtime
    root = Path(root).resolve()
    if not isinstance(configuration, dict):
        raise ValueError('Research configuration must be an object')
    if authorization_note is not None and not isinstance(authorization_note, str):
        raise ValueError('Configuration update note must be text')
    explicit_authorization = bool(authorization_note and authorization_note.strip())
    with _writer(root):
        values = revisions(root)
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision != len(values)):
            raise ValueError('Configuration revision changed; reload the configuration before saving')
        settings = research_config.validate_update(root, configuration, base=base)
        fork_runtime.preflight_selected_settings(root, settings)
        document = research_config.to_document(root, settings)
        previous = editable(root)
        state = status(root)
        events, _ = budget._events(root)
        budget_baseline = budget.authorization_revision(root)
        resource_baseline = _resource_authorization_revision(root, events)
        reauthorized = bool(explicit_authorization and values and state['pending'] and (
            values[-1].get('budget_changes') and values[-1].get('budget_baseline') != budget_baseline
            or values[-1].get('resource_change') and values[-1].get('resource_baseline') != resource_baseline))
        if settings == previous and not reauthorized:
            return dict(status(root), status='configuration_unchanged')
        revision = len(values) + 1
        directory = _directory(root) / 'contracts' / f'{revision:06d}'
        for key, module, method in (('formal_review_policy', formal_review, 'freeze_contract'),
                                     ('execution_authority_policy', execution_authority, 'freeze_configuration')):
            if settings.get(key) is not None:
                if previous.get(key) is not None:
                    settings[key] = copy.deepcopy(previous[key])
                else:
                    settings[key] = getattr(module, method)(root, directory=directory)
        before = budget.ceilings(root)
        if (not authorization_note and values
                and all(settings[key] == previous[key] for key in ('max_tokens', 'max_seconds', 'max_steps'))
                and settings.get('execution_resource_policy') == previous.get('execution_resource_policy')):
            # Retain the actual earlier operator statement when another edit
            # preserves the same still-pending budget adjustment.
            authorization_note = values[-1]['authorization_note']
        changed = {key: settings[key] for key in ('max_tokens', 'max_seconds', 'max_steps') if settings[key] != before[key]}
        active_resource = copy.deepcopy(state['active_settings'].get('execution_resource_policy'))
        if active_resource is not None:
            accounting = budget.execution_accounting(root)
            if accounting['grant'] is not None:
                active_resource['allowance'] = dict(accounting['grant']['allowance'])
        resource_changed = settings.get('execution_resource_policy') != active_resource
        if resource_changed and (not isinstance(authorization_note, str) or not authorization_note.strip()):
            raise ValueError('Execution resource changes require an operator adjustment note')
        if changed:
            budget._validate_amendment(root, **_budget_amendment_arguments(changed), authorization_note=authorization_note,
                                      authorization_statement=authorization_note, applicable_settings=settings)
        if values and state['pending'] and not explicit_authorization:
            # A model-only edit keeps the original pending authorization, rather
            # than silently rebasing its older amounts onto an intervening CLI edit.
            if all(settings[key] == previous[key] for key in ('max_tokens', 'max_seconds', 'max_steps')):
                budget_baseline = values[-1].get('budget_baseline')
            if settings.get('execution_resource_policy') == previous.get('execution_resource_policy'):
                resource_baseline = values[-1].get('resource_baseline')
        path = _directory(root) / 'revisions' / f'{revision:06d}.json'
        predecessor = (_reference(root, values[-1]) if values else
                       dict(path='settings.json'))
        predecessor.pop('revision', None)
        row = dict(version=1, revision=revision, predecessor=predecessor, settings=settings,
                   configuration=document, budget_changes=changed, resource_change=resource_changed,
                   budget_baseline=budget_baseline, resource_baseline=resource_baseline,
                   authorization_note=(authorization_note or '').strip())
        staged = path.with_suffix('.pending')
        c.write(staged, row)
        identity = _directory(root) / 'identities' / path.name
        c.write(identity, dict(path=str(path)))
        identity.chmod(0o400)
        staged.chmod(0o400)
        os.replace(staged, path)
        from . import accepted_budget
        accepted_budget.accept_configuration(root, dict(path=path.relative_to(root).as_posix()), row)
        return dict(status(root), status='configuration_saved')


def bind_new_job(root, step):
    """Independent binding entry; build owners consume bind_new_job_facts."""
    revision, _, _ = bind_new_job_facts(root, step, base=c.settings(root))
    return revision


def bind_new_job_facts(root, step, *, base):
    """Reserve an exact snapshot before any new task inputs are constructed.

    A crash retains this reservation, and budget amendment uses an idempotency
    key. Subsequent saves cannot change the policy of the in-progress task.
    """
    from . import budget, fork_runtime
    root = Path(root).resolve()
    with budget._operator_writer_from_base(root, base=base), _writer_from_base(root, base=base):
        from . import accepted_budget
        head = accepted_budget.recover(root, accepted_budget.load(root, base=base))
        path = root / 'jobs' / f'{step:03d}.json'
        if path.exists():
            job = c.read(c.regular(path))
            return job.get('configuration_revision'), c.settings_for_job(root, job, base=base, head=head), head
        reserved = _reservation(root, step, head=head)
        if reserved is not None:
            return reserved['configuration_revision'], policy_for_job(root, reserved, base=base, head=head), head
        current = _current_revision(root, head=head)
        values = [] if current is None else [current]
        settings = copy.deepcopy(current['settings']) if current is not None else base
        fork_runtime.preflight_selected_settings(root, settings, step=step)
        ref = _reference(root, values[-1]) if values else None
        if values and values[-1]['revision'] > head['applied_configuration']:
            row = values[-1]
            changed = row.get('budget_changes', {})
            operation = f'configuration-{row["revision"]}'
            resource_operation = operation + '-resources'
            events, _ = budget._events_from_state(head)
            prior = events.state.row('operation/'+operation)
            applied = events.state.event(prior['ref']) if prior is not None else None
            prior = events.state.row('operation/'+resource_operation)
            resource_applied = events.state.event(prior['ref']) if prior is not None else None
            # Check both predecessors before applying either part of this save.
            # Matching numeric values do not erase a later operator decision.
            if changed and applied is None and row.get('budget_baseline') != budget._authorization_revision(root, events):
                raise ValueError('Pending configuration budget conflicts with a later amendment; reload and save the configuration')
            if row.get('resource_change') and resource_applied is None and row.get('resource_baseline') != _resource_authorization_revision(root, events):
                raise ValueError('Pending configuration execution resources conflict with a later amendment; reload and save the configuration')
            if applied is not None and any(
                    (applied.get('after_max_steps') if key == 'max_steps' else applied['after'][key]) != amount
                    for key, amount in changed.items()):
                raise ValueError('Configuration budget amendment operation changed')
            if resource_applied is not None and resource_applied['allowance'] != (settings.get('execution_resource_policy') or {}).get('allowance'):
                raise ValueError('Configuration execution resource amendment operation changed')
            if changed and applied is None:
                budget._amend_locked(root, **_budget_amendment_arguments(changed), authorization_note=row['authorization_note'],
                                     authorization_statement=row['authorization_note'], operation_id=operation,
                                     applicable_settings=settings)
            if row.get('resource_change') and settings.get('execution_resource_policy') is not None and resource_applied is None:
                budget.amend_execution_grant(root, allowance=settings['execution_resource_policy']['allowance'],
                    authorization_note=row['authorization_note'], operation_id=resource_operation)
        binding = _directory(root) / 'bindings' / f'{step:06d}.json'
        head = accepted_budget.accept_configuration_binding_from_base(root, step,
            dict(step=step, configuration_revision=ref), base=base)
        binding.chmod(0o400)
        return ref, settings, head
