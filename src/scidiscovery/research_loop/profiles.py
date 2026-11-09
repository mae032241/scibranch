"""Frozen role model choices; omitted roles and fields inherit global defaults."""
from __future__ import annotations

ROLES = ('H', 'W', 'A', 'E')
FIELDS = ('model', 'reasoning_effort', 'service_tier')


def normalize(value, defaults, workers=None, skill_sets=None):
    base = {key: defaults[key] for key in FIELDS}
    if (not isinstance(base['model'], str) or not base['model'].strip()
            or any(item is not None and (not isinstance(item, str) or not item.strip())
                   for key,item in base.items() if key!='model')):
        raise ValueError('A model and valid optional reasoning effort and service tier are required')
    if not isinstance(value, dict) or set(value) - set(ROLES):
        raise ValueError('Role profiles must name only H, W, A or E')
    workers = {} if workers is None else workers
    if not isinstance(workers, dict):
        raise ValueError('Named Worker profiles must be a table')
    allowed = set(FIELDS) | {'skill_set', 'subagents'}
    for name, worker in workers.items():
        if (not isinstance(name, str) or not name.strip() or not isinstance(worker, dict)
                or set(worker) - allowed):
            raise ValueError('Named Worker profile fields are unsupported')
        if 'subagents' in worker and type(worker['subagents']) is not bool:
            raise ValueError('Native subagents profile declaration must be boolean')
    result = {}
    for role in ROLES:
        override = value.get(role, {})
        if not isinstance(override, dict):
            raise ValueError('Role profile must be a table')
        worker_name = override.get('worker')
        if worker_name is not None and (not isinstance(worker_name, str) or worker_name not in workers):
            raise ValueError('Role profile references an unknown named Worker')
        override = {**(workers[worker_name] if worker_name is not None else {}), **override}
        if (set(override) - (allowed | {'worker'})
                or any(not (item is None and key!='model' and base[key] is None)
                       and (not isinstance(item, str) or not item.strip()) for key,item in override.items() if key in FIELDS)):
            raise ValueError('Role profile fields must be nonempty model, reasoning_effort or service_tier strings')
        name = override.get('skill_set')
        if 'subagents' in override and type(override['subagents']) is not bool:
            raise ValueError('Native subagents profile declaration must be boolean')
        if name is not None and (not isinstance(name, str) or skill_sets is None or name not in skill_sets):
            raise ValueError('Role profile references an unknown Skill set')
        result[role] = {**base, **override}
    return result


def selected(settings, role):
    if role not in ROLES:
        raise ValueError('Unknown scientific role for native profile')
    return {key: value for key, value in normalize(settings.get('role_profiles', {}), settings,
        settings.get('worker_profiles'), (settings.get('skills') or {}).get('sets', {}))[role].items() if key in FIELDS}


def for_job(settings, job):
    from . import skill_supply
    skill_supply.for_job(settings, job)
    profile = selected(settings, job['role'])
    if (settings.get('role_profiles') is not None or 'native_profile' in job) and job.get('native_profile') != profile:
        raise ValueError('Frozen native job profile changed')
    native_subagents(settings, job)
    return profile


def native_subagents(settings, job):
    """Validate one role permission without changing the model-only profile."""
    value = normalize(settings.get('role_profiles', {}), settings,
        settings.get('worker_profiles'), (settings.get('skills') or {}).get('sets', {}))[job['role']]
    if 'subagents' not in value and 'native_subagents' not in job:
        return None
    expected = value.get('subagents')
    if (type(expected) is not bool or type(job.get('native_subagents')) is not bool
            or job['native_subagents'] is not expected):
        raise ValueError('Frozen native job subagent permission changed')
    return expected


def binding(settings, job):
    profile = for_job(settings, job)
    result = dict(native_profile=profile)
    if 'native_subagents' in job:
        result['native_subagents'] = native_subagents(settings, job)
    if 'skill_binding' in job:
        result['skill_binding'] = job['skill_binding']
    return result


def validate_intent(settings, job, intent):
    profile = for_job(settings, job)
    if intent.get('skill_binding') != job.get('skill_binding'):
        raise ValueError('Frozen native intent Skill binding changed')
    if ('native_profile' in job or 'native_profile' in intent) and intent.get('native_profile') != profile:
        raise ValueError('Frozen native intent profile changed')
    if ('native_subagents' in job) != ('native_subagents' in intent) or (
            'native_subagents' in job and (type(intent['native_subagents']) is not bool
                or intent['native_subagents'] is not job['native_subagents'])):
        raise ValueError('Frozen native intent subagent permission changed')
    return profile
