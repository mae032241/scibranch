"""One current research-loop protocol, with no old-instance interpretation.

Advance CURRENT when frozen scientific interpretation changes. A protocol marker
is compatibility information, never permission or scientific qualification.
"""
from pathlib import Path


CURRENT = 33


class UnsupportedProtocol(ValueError):
    """An incompatible instance or request cannot enter the current runtime."""

    code = 'unsupported_protocol'

    def __init__(self, subject):
        self.subject = subject
        super().__init__(f'{subject} requires current research-loop protocol {CURRENT}; unsupported format, no migration')


def _current(value, subject):
    if type(value) is not int or value != CURRENT:
        raise UnsupportedProtocol(subject)


def require_settings_format(settings):
    """Reject unsupported frozen budgets without supplying missing policies."""
    if not isinstance(settings, dict):
        raise UnsupportedProtocol('Instance')
    _current(settings.get('handoff_protocol'), 'Instance')
    policy = settings.get('final_a_completion_policy')
    if (settings.get('node_budget_mode') != 'soft'
            or not isinstance(policy, dict) or set(policy) != {'version'}
            or type(policy['version']) is not int or policy['version'] != 1):
        raise UnsupportedProtocol('Instance budget policy')
    return settings


def require_job_format(job):
    """Only the current scientific view can enter retained-job consumers."""
    if not isinstance(job, dict):
        raise UnsupportedProtocol('Scientific job')
    _current(job.get('handoff_protocol'), 'Scientific job')
    for field in ('worker_view_version', 'lineage_projection_version'):
        if type(job.get(field)) is not int or job[field] != 1:
            raise UnsupportedProtocol('Scientific job view')
    return job


def require_scope(scope):
    """Check an already host-bound scope inside a restricted adapter namespace."""
    if not isinstance(scope, dict):
        raise UnsupportedProtocol('Execution scope')
    _current(scope.get('handoff_protocol'), 'Execution scope')
    return scope


def require_current(root, job=None):
    """Read verified settings and, when supplied, the exact retained job.

    No lock, directory, ledger, default feature or migration is created here.
    Call again at the actual write/launch boundary after an earlier preflight.
    """
    from . import contracts as c

    root = Path(root)
    base = c.settings(root)
    require_current_base(root, base=base)
    if job is None:
        return base
    if not isinstance(job, dict):
        raise UnsupportedProtocol('Scientific job')
    _current(job.get('handoff_protocol'), 'Scientific job')
    step = job.get('step')
    if type(step) is not int or step <= 0:
        raise ValueError('An exact frozen scientific job is required')
    retained = c.read(c.regular(root / 'jobs' / f'{step:03d}.json'))
    _require_retained_job(job, retained=retained)
    from . import accepted_budget
    head = accepted_budget.load(root, base=base) if job.get('configuration_revision') is not None else None
    policy = c.settings_for_job(root, job, base=base, head=head)
    return require_settings_format(policy)


def require_current_base(root, *, base):
    """Validate the caller's base fact and the live owner, without loading it."""
    require_settings_format(base)
    from .instance_owner import require_owner
    require_owner(root)
    return base


def require_current_job(root, *, base, job, retained, policy):
    """Consume the exact job and selected policy produced by this operation."""
    require_current_base(root, base=base)
    _require_retained_job(job, retained=retained)
    return require_settings_format(policy)


def _require_retained_job(job, *, retained):
    from . import contracts as c
    if not isinstance(job, dict):
        raise UnsupportedProtocol('Scientific job')
    _current(job.get('handoff_protocol'), 'Scientific job')
    if type(job.get('step')) is not int or job['step'] <= 0:
        raise ValueError('An exact frozen scientific job is required')
    _current(retained.get('handoff_protocol'), 'Retained scientific job')
    if c.job_record(retained) != c.job_record(job):
        raise ValueError('Scientific job differs from its frozen original')
    return require_job_format(job)



def stage_scope(root,job):
    """Read the stable identity of an exact retained current scientific job.

    This is not effect permission: host writers still use require_current and
    the owner lease. Restricted adapters can compare their host-frozen scope
    without reading private settings or traversing the native attempt policy.
    """
    from . import contracts as c
    require_job_format(job)
    if (type(job.get('step')) is not int or job['step']<=0
            or job.get('role') not in {'H','W','A','E'}):
        raise ValueError('An exact frozen scientific job is required')
    root=Path(root).absolute();path=root/'jobs'/f'{job["step"]:03d}.json'
    if root.resolve()!=root or path.resolve()!=path:
        raise ValueError('Scientific job requires its canonical original path')
    if c.read(c.regular(path))!=c.job_record(job):
        raise ValueError('Scientific job differs from its frozen original')
    reference=c.record_ref(path)
    return stage_scope_for_job(root,job,task_ref=reference)


def stage_scope_for_job(root,job,*,task_ref):
    """Project the canonical job identity already accepted by this owner."""
    require_job_format(job)
    # Attempt zero's execution identity is the original job reference, including when
    # later native attempts exist. Scope identity must not load their policies.
    return dict(step=job['step'],role=job['role'],handoff_protocol=job['handoff_protocol'],
        job_ref=task_ref,execution_key=task_ref,
        mechanism_ref=(None if (job.get('search') or {}).get('breadth') is not None
            else ((job.get('search') or {}).get('focus') or {}).get('mechanism_ref')))
