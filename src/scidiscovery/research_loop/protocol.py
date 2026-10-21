"""One current write protocol; archive readers do not call these guards.

Advance CURRENT when frozen scientific interpretation changes. A protocol marker
is compatibility information, never permission or scientific qualification.
"""
from pathlib import Path


CURRENT = 23


class UnsupportedProtocol(ValueError):
    """An archive or incompatible request cannot enter a current write path."""

    code = 'unsupported_protocol'

    def __init__(self, subject):
        self.subject = subject
        super().__init__(f'{subject} requires current research-loop protocol {CURRENT}; preserved without migration')


def _current(value, subject):
    if type(value) is not int or value != CURRENT:
        raise UnsupportedProtocol(subject)


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
    settings = c.settings(root)
    from .instance_owner import require_owner
    require_owner(root)
    _current(settings.get('handoff_protocol'), 'Instance')
    if job is not None:
        if not isinstance(job, dict):
            raise UnsupportedProtocol('Scientific job')
        _current(job.get('handoff_protocol'), 'Scientific job')
        step = job.get('step')
        if type(step) is not int or step <= 0:
            raise ValueError('An exact frozen scientific job is required')
        retained = c.read(c.regular(root / 'jobs' / f'{step:03d}.json'))
        _current(retained.get('handoff_protocol'), 'Retained scientific job')
        if retained != job:
            raise ValueError('Scientific job differs from its frozen original')
    return c.settings(root, job) if job is not None else settings



def stage_scope(root,job):
    """Read the stable identity of an exact retained current scientific job.

    This is not effect permission: host writers still use require_current and
    the owner lease. Restricted adapters can compare their host-frozen scope
    without reading private settings or traversing the native attempt policy.
    """
    from . import contracts as c
    if not isinstance(job,dict):raise UnsupportedProtocol('Scientific job')
    _current(job.get('handoff_protocol'),'Scientific job')
    if (type(job.get('step')) is not int or job['step']<=0
            or job.get('role') not in {'H','W','A','E'}):
        raise ValueError('An exact frozen scientific job is required')
    root=Path(root).absolute();path=root/'jobs'/f'{job["step"]:03d}.json'
    if root.resolve()!=root or path.resolve()!=path:
        raise ValueError('Scientific job requires its canonical original path')
    if c.read(c.regular(path))!=job:
        raise ValueError('Scientific job differs from its frozen original')
    digest=c.digest(path)
    # Attempt zero's execution identity is the original job hash, including when
    # later native attempts exist. Scope identity must not load their policies.
    return dict(step=job['step'],role=job['role'],handoff_protocol=job['handoff_protocol'],
        job_sha256=digest,execution_key=digest,
        mechanism_ref=(None if (job.get('search') or {}).get('breadth') is not None
            else ((job.get('search') or {}).get('focus') or {}).get('mechanism_ref')))

def archive_status(root):
    """Preserved original facts only; no schema replay, observer, cache or page writes."""
    from . import contracts as c
    import json
    root=Path(root)
    settings=c.settings(root)
    nodes=[];usage=dict(input_tokens=0,cached_input_tokens=0,output_tokens=0)
    unknown=[];omitted_native_records=0
    jobs=sorted((root/'jobs').glob('*.json'))
    omitted_nodes=max(0,len(jobs)-256)
    for path in jobs[:256]:
        job=c.read(c.regular(path,limit=None))
        step=job['step'];record=c.host_record(c.workspace(root,step))
        state='prepared';seal=record/'result-identity.json'
        if seal.exists():
            identity=c.read(c.regular(seal,limit=None))
            attempt=(identity.get('execution_ref') or {}).get('attempt',0)
            if type(attempt) is not int or attempt<0:raise ValueError('Preserved native reference is malformed')
            terminal=record/'continuations'/f'{attempt:03d}' if attempt else record
            if (c.digest(c.regular(path))!=identity['job_sha256']
                    or c.digest(c.regular(record/'result.json'))!=identity['sha256']
                    or c.digest(c.regular(terminal/'science-output.json',limit=None))!=identity['raw_sha256']):
                raise ValueError('Preserved scientific originals changed')
            state='seal_present_originals_verified'
        elif (record/'native-outcome.json').exists():state='native_outcome_unsealed'
        elif (record/'native-intent.json').exists():state='native_intent_present_unknown'
        chain=[record]
        directory=record/'continuations'
        if directory.exists():
            if directory.resolve()!=directory:raise ValueError('Preserved continuation directory changed')
            continuations=sorted(directory.iterdir())
            omitted_native_records+=max(0,len(continuations)-256)
            chain+=continuations[:256]
        for native_record in chain:
            if native_record.resolve()!=native_record or not native_record.is_dir():raise ValueError('Preserved native record changed')
            if not (native_record/'native-intent.json').exists():continue
            meter=native_record/'usage.json'
            if not meter.exists():
                if step not in unknown:unknown.append(step)
                continue
            value=c.read(c.regular(meter,limit=16_000))
            if not isinstance(value,dict) or any(type(value.get(key)) is not int or value[key]<0 for key in usage):
                raise ValueError('Preserved usage original is malformed')
            for key in usage:usage[key]+=value[key]
        nodes.append(dict(step=step,role=job['role'],state=state,scientific_qualification='not_reinterpreted'))
    return dict(status='preserved_read_only',protocol=settings.get('handoff_protocol'),current_protocol=CURRENT,
        goal_id=settings.get('goal_id'),paused=(root/'pause.request').exists(),nodes=nodes,
        omitted_nodes=omitted_nodes,omitted_native_records=omitted_native_records,partial_usage=bool(unknown or omitted_nodes or omitted_native_records),
        usage=dict(**usage,observed_tokens=usage['input_tokens']+usage['output_tokens'],unknown_steps=unknown,
            coverage='partial' if unknown or omitted_nodes or omitted_native_records else 'complete'),
        goal_completion='not_asserted',history='Existing original pages retained; absent pages are not regenerated.')
