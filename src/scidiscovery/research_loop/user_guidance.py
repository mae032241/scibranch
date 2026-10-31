"""Mutable pending user advice, frozen once at an exact new scientific task."""
from contextlib import contextmanager
from pathlib import Path
import uuid

from . import budget, contracts


def _path(root, guidance_id):
    if not isinstance(guidance_id, str) or len(guidance_id) != 32 or any(c not in '0123456789abcdef' for c in guidance_id):
        raise ValueError('Exact guidance ID required')
    return Path(root) / 'host-control' / 'guidance' / f'{guidance_id}.json'


def _validate(text, target, kind, effective_step, scope):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Nonempty user guidance required')
    if scope not in {'next', 'research'}:
        raise ValueError('Guidance scope must be next or research')
    if target not in {'global', 'H', 'W', 'A', 'E'} or kind not in {'advice', 'goal_change'}:
        raise ValueError('Guidance requires an explicit supported target and type')
    if effective_step is not None and (type(effective_step) is not int or effective_step < 1):
        raise ValueError('Effective step must be a positive exact node number')
    if kind == 'goal_change' and (effective_step is None or target != 'global'):
        raise ValueError('A goal change requires an explicit effective step and global target')


def list_guidance(root):
    root = Path(root).resolve()
    with budget._ledger_writer(root):
        values = [contracts.read(contracts.regular(path)) for path in sorted((root / 'host-control' / 'guidance').glob('*.json'))]
        for value in values:
            path = root / 'host-control' / 'guidance-revocations' / f'{value['guidance_id']}.json'
            if path.exists():
                value['future_revocation'] = contracts.read(contracts.regular(path))
        return sorted(values, key=lambda row: row.get('sequence', 0))


def submit(root, text, *, target='global', kind='advice', effective_step=None, scope='next'):
    root = Path(root).resolve()
    if kind == 'goal_change':
        scope = 'research'
    _validate(text, target, kind, effective_step, scope)
    value = dict(guidance_id=uuid.uuid4().hex, revision=1, status='pending', text=text.strip(),
                 target=target, kind=kind, effective_step=effective_step, scope=scope)
    with budget._ledger_writer(root):
        value['sequence'] = 1 + len(list((root / 'host-control' / 'guidance').glob('*.json')))
        contracts.write(_path(root, value['guidance_id']), value)
    return value


def _pending(root, guidance_id, expected_revision):
    value = contracts.read(contracts.regular(_path(root, guidance_id)))
    if type(expected_revision) is not int or value['revision'] != expected_revision:
        raise ValueError('Pending guidance revision changed; read the current revision first')
    if value['status'] != 'pending':
        raise ValueError('Applied or revoked guidance is immutable')
    # A task written immediately before a controller interruption is already bound.
    for path in (Path(root) / 'jobs').glob('*.json'):
        if any(row['guidance_id'] == guidance_id for row in contracts.read(contracts.regular(path)).get('user_guidance', [])):
            raise ValueError('Guidance is already bound to an immutable scientific task')
    return value


def edit(root, guidance_id, text, *, expected_revision):
    root = Path(root).resolve()
    with budget._ledger_writer(root):
        value = _pending(root, guidance_id, expected_revision)
        _validate(text, value['target'], value['kind'], value['effective_step'], value['scope'])
        value.update(text=text.strip(), revision=value['revision'] + 1)
        contracts.write(_path(root, guidance_id), value)
    return value


def revoke(root, guidance_id, *, expected_revision):
    root = Path(root).resolve()
    with budget._ledger_writer(root):
        value = contracts.read(contracts.regular(_path(root, guidance_id)))
        if type(expected_revision) is not int or value['revision'] != expected_revision:
            raise ValueError('Guidance revision changed')
        if value['status'] == 'applied' and value.get('scope') == 'research':
            path = root / 'host-control' / 'guidance-revocations' / f'{guidance_id}.json'
            if path.exists():
                return contracts.read(contracts.regular(path))
            receipt = dict(guidance_id=guidance_id, revision=expected_revision, status='revoked',
                           revocation_id=uuid.uuid4().hex,
                           effective_step=1 + max((int(path.stem) for path in (root / 'jobs').glob('*.json')), default=0))
            contracts.write(path, receipt)
            return receipt
        value = _pending(root, guidance_id, expected_revision)
        value.update(status='revoked', revision=value['revision'] + 1)
        contracts.write(_path(root, guidance_id), value)
    return value


@contextmanager
def binding(root, step, role, retained):
    """Hold the existing short ledger lock through task publication and application."""
    with budget._ledger_writer(root):
        values = []
        if retained is not None:
            values = retained.get('user_guidance', [])
        else:
            for path in sorted((Path(root) / 'host-control' / 'guidance').glob('*.json')):
                value = contracts.read(contracts.regular(path))
                revoked = Path(root) / 'host-control' / 'guidance-revocations' / f'{value["guidance_id"]}.json'
                if revoked.exists() and contracts.read(contracts.regular(revoked))['effective_step'] <= step:
                    continue
                if (value['status'] == 'applied' and value.get('scope') == 'research'
                        and value['target'] in {'global', role} and (value['effective_step'] or 1) <= step):
                    values.append(value)
                elif (value['status'] == 'pending'
                        and value['target'] in {'global', role} and (value['effective_step'] or 1) <= step):
                    values.append(dict(value, status='applied', applied_step=step, applied_role=role))
        values.sort(key=lambda row: row.get('sequence', 0))
        yield values


def apply_job(root, job):
    """Apply the advice bound to this published task under its accept lock.

    A retained task repeats this exact write after an interrupted publication;
    no other job needs to be opened to establish its binding.
    """
    for value in job.get('user_guidance', []):
        path = _path(root, value['guidance_id'])
        current = contracts.read(contracts.regular(path))
        if current['revision'] != value['revision']:
            raise ValueError('Frozen guidance revision changed')
        if current['status'] == 'pending':
            contracts.write(path, value)


def public(job):
    if not job.get('user_guidance'):
        return {}
    return dict(user_guidance=dict(
        instruction='These are user statements. Preserve the original goal and distinguish explicit goal changes from soft advice. They grant no execution permission, scientific qualification or mandatory scientific choice. You may decline advice for scientific reasons in your ordinary reasoning.',
        statements=[{key: row[key] for key in ('text', 'kind', 'target', 'effective_step', 'scope')}
                    for row in job['user_guidance']]))
