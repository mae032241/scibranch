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
    from .instance_owner import lease
    from .protocol import require_current
    root = Path(root).resolve()
    with lease(root):
        require_current(root)
        fd = os.open(root / 'configuration.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
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


def _read_reference(root, ref):
    if not isinstance(ref, dict) or set(ref) != {'revision', 'path'} or type(ref.get('revision')) is not int or ref['revision'] < 1:
        raise ValueError('Exact configuration revision reference required')
    expected = f'host-control/configuration/revisions/{ref["revision"]:06d}.json'
    if ref['path'] != expected:
        raise ValueError('Configuration revision location changed')
    path = c.regular(Path(root) / expected)
    values = revisions(root, through=ref['revision'])
    if ref['revision'] > len(values):
        raise ValueError('Configuration revision is missing')
    return copy.deepcopy(values[ref['revision'] - 1]['settings'])


def settings_path(root, job):
    """Exact policy original for recovery/export references."""
    for_job(root, job)
    return Path(root) / ((job.get('configuration_revision') or {}).get('path', 'settings.json'))


def for_job(root, job):
    """A retained node without a revision always uses its original base settings."""
    root = Path(root).resolve()
    ref = job.get('configuration_revision')
    return c.settings(root) if ref is None else _read_reference(root, ref)


def for_step(root, step):
    root = Path(root).resolve()
    return for_job(root, c.read(c.regular(root / 'jobs' / f'{step:03d}.json')))


def _reservation(root, step):
    path = _directory(root) / 'bindings' / f'{step:06d}.json'
    if not path.exists():
        return None
    row = c.read(c.regular(path))
    if set(row) != {'step', 'configuration_revision'} or row['step'] != step:
        raise ValueError('Configuration node binding changed')
    if row['configuration_revision'] is not None:
        _read_reference(root, row['configuration_revision'])
    return row


def for_new_job(root, step):
    root = Path(root).resolve()
    path = root / 'jobs' / f'{step:03d}.json'
    if path.exists():
        return for_job(root, c.read(c.regular(path)))
    reserved = _reservation(root, step)
    return for_job(root, reserved) if reserved is not None else current(root)


def current(root):
    """Pure policy read: latest saved snapshot, without budget admission reads."""
    values = revisions(root)
    return copy.deepcopy(values[-1]['settings']) if values else c.settings(Path(root))


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
            active_ref = _reservation(root, step)['configuration_revision']
    settings = editable(root)
    active = for_job(root, dict(configuration_revision=active_ref))
    return dict(status='configuration_inspected', revision=latest,
                active_revision=(active_ref or {}).get('revision', 0), pending=latest != (active_ref or {}).get('revision', 0),
                effective_from='next_new_node', configuration=research_config.to_document(root, settings),
                settings=settings, active_settings=active)


def _resource_authorization_revision(root, events):
    from . import budget
    # Initial seeding only materializes the frozen grant; later grants revise it.
    changes = [event for event in events if event['kind'] == 'resource_grant'
               and (event.get('previous_grant') is not None or event.get('operation_id') is not None)]
    return budget._resource_ref(root, changes[-1]) if changes else None


def submit(root, configuration, *, base=None, expected_revision=None,
           authorization_note=None):
    """Validate and queue a complete declaration using optimistic concurrency."""
    from . import research_config, budget, formal_review, execution_authority
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
        document = research_config.to_document(root, settings)
        previous = editable(root)
        state = status(root)
        events, _ = budget._events(root, c.settings(root))
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
            budget._validate_amendment(root, **changed, authorization_note=authorization_note,
                                      authorization_statement=authorization_note)
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
        return dict(status(root), status='configuration_saved')


def bind_new_job(root, step):
    """Reserve an exact snapshot before any new task inputs are constructed.

    A crash retains this reservation, and budget amendment uses an idempotency
    key. Subsequent saves cannot change the policy of the in-progress task.
    """
    from . import budget
    root = Path(root).resolve()
    with budget._operator_writer(root), _writer(root):
        path = root / 'jobs' / f'{step:03d}.json'
        if path.exists():
            job = c.read(c.regular(path))
            return job.get('configuration_revision')
        reserved = _reservation(root, step)
        if reserved is not None:
            return reserved['configuration_revision']
        values = revisions(root)
        ref = _reference(root, values[-1]) if values else None
        previously_bound = {(_reservation(root, int(path.stem))['configuration_revision'] or {}).get('revision', 0)
                            for path in (_directory(root) / 'bindings').glob('*.json')}
        if values and values[-1]['revision'] not in previously_bound:
            row = values[-1]
            settings = row['settings']
            changed = row.get('budget_changes', {})
            operation = f'configuration-{row["revision"]}'
            resource_operation = operation + '-resources'
            events, _ = budget._events(root, c.settings(root))
            applied = next((event for event in events if event['kind'] == 'budget_amend'
                            and event.get('operation_id') == operation), None)
            resource_applied = next((event for event in events if event['kind'] == 'resource_grant'
                                     and event.get('operation_id') == resource_operation), None)
            # Check both predecessors before applying either part of this save.
            # Matching numeric values do not erase a later operator decision.
            if changed and applied is None and row.get('budget_baseline') != budget.authorization_revision(root):
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
                budget._amend_locked(root, **changed, authorization_note=row['authorization_note'],
                                     authorization_statement=row['authorization_note'], operation_id=operation)
            if row.get('resource_change') and settings.get('execution_resource_policy') is not None and resource_applied is None:
                budget.amend_execution_grant(root, allowance=settings['execution_resource_policy']['allowance'],
                    authorization_note=row['authorization_note'], operation_id=resource_operation)
        binding = _directory(root) / 'bindings' / f'{step:06d}.json'
        c.write(binding, dict(step=step, configuration_revision=ref))
        binding.chmod(0o400)
        return ref
