"""Pure immutable pre-delivery scope carried by the existing return binding."""
KINDS = frozenset({'delivery_review', 'delivery_correction'})


def binding(job):
    value = job.get('evidence_return')
    if not isinstance(value, dict) or value.get('kind') not in KINDS:
        return None
    if (job.get('research_strategy_version') not in (2, 3) or set(value) != {'kind', 'step', 'corrections_used'}
            or type(value['step']) is not int or not 0 < value['step'] < job['step']
            or type(value['corrections_used']) is not int or value['corrections_used'] not in (0, 1)
            or job['role'] != ('A' if value['kind'] == 'delivery_review' else 'W')
            or value['kind'] == 'delivery_correction' and value['corrections_used'] != 1
            or job.get('purpose') is not None or job.get('formal_review') or job.get('final_only')
            or job.get('final_report') or job.get('engineering_repair') or job.get('author_correction')
            or job.get('correction_request')):
        raise ValueError('Invalid immutable delivery review scope')
    return dict(value)


def scope(job):
    value = binding(job)
    return value['kind'] if value is not None else None


def make(kind, step, corrections_used):
    return dict(kind=kind, step=step, corrections_used=corrections_used)


def validate_parent(job, facts):
    """Validate only the exact direct parent already loaded by the job owner."""
    value = binding(job)
    if value is None:
        return
    source = facts.get(value['step'])
    if source is None or ('direct_refs' in job and not any(
            ref['path']==f'inputs/parents/{value["step"]:03d}/conclusion.json' for ref in job['direct_refs'])):
        raise ValueError('Delivery review requires its exact direct source')
    parent, payload = source['job'], source['payload']
    prior = binding(parent)
    if parent.get('research_strategy_version') != job.get('research_strategy_version'):
        raise ValueError('Delivery review cannot upgrade an original scientific task')
    if value['kind'] == 'delivery_review':
        valid = (parent['role'] == 'W' and (
            value['corrections_used'] == 0 and prior is None and payload.get('stage_intent') == 'decision'
            or value['corrections_used'] == 1 and prior is not None and prior['kind'] == 'delivery_correction'
                and payload.get('stage_intent') in {'decision', 'continue'}))
        valid = valid or (parent['role']=='A' and prior is None and value['corrections_used']==0
            and payload.get('next_action') in {'finish','deliver'}
            and not parent.get('purpose') and not parent.get('formal_review')
            and not parent.get('final_only') and not parent.get('final_report'))
    else:
        valid = (prior is not None and (
            parent['role'] == 'A' and prior['kind'] == 'delivery_review' and prior['corrections_used'] == 0
                and payload.get('next_action') == 'continue_w'
            or parent['role'] == 'W' and prior['kind'] == 'delivery_correction'
                and payload.get('stage_intent') in {'handoff', 'continue'}))
    if not valid:
        raise ValueError('Delivery review scope or correction allowance changed')
