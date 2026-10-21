"""Scientific A requests and exact completed-request replay; no value classifier."""
from . import contracts as c, correction, history

ACTIONS = {'continue_w': 'W', 'revise_h': 'H', 'extract': 'E'}


def schema():
    fields = dict(decision=dict(type='string', enum=['candidate', 'mechanism', 'compliance']),
                  question=dict(type='string'),
                  change_criterion=dict(type='string'))
    return dict(type=['object', 'null'], properties=fields, required=list(fields), additionalProperties=False,
        description='For continue_w, revise_h or extract, state the scientific decision, concrete question '
                    'and what result would change that decision. Null is allowed for audit, wait or finish. '
                    'No evidence paths or hashes: the host binds the actual mechanism and assessed originals.')


def _key(job, payload, focus):
    request = c.scientific_field(job,payload,'decision_request')
    mechanism = focus.get('mechanism_ref') if focus else None
    candidate = payload.get('candidate_recommendation') if request['decision'] == 'candidate' else None
    return (payload['next_action'], request['decision'], request['question'].strip(),
            request['change_criterion'].strip(), mechanism, candidate)


def _basis(root, job):
    # A scientific clarification keeps the original assessed work, not its own prose.
    if job.get('author_correction'):
        original = history._source(root, job['author_correction']['root']['step'])[0]
        return correction._proofs(root, original, {'verdict': 'progress'})
    return correction._proofs(root, job, {'verdict': 'progress'})


def _completed(job, payload, sources):
    response = sources.get(job['step'] + 1)
    if response is None or response[0]['role'] != ACTIONS.get(payload['next_action']):
        return False
    following = response[0]
    if not any(ref['path'] == f'inputs/parents/{job["step"]:03d}/conclusion.json' for ref in following['direct_refs']):
        return False
    if following['role'] == 'E' and following.get('evidence_return') != dict(step=job['step'], role='A'):
        return False
    return True


def assess(root, job, payload, *, focus=None):
    if c.settings(root, job).get('decision_contract_version')!=1 or job['role'] != 'A' or job.get('purpose') in {'closeout','formal_review'}:
        return []
    if payload['next_action'] not in ACTIONS:
        return []  # Necessary audit, pause and delivery choices are independent.
    request = c.scientific_field(job,payload,'decision_request')
    if request is None or any(not request[key].strip() for key in ('question', 'change_criterion')):
        return [dict(code='a_decision_request_missing', subject=payload['next_action'])]
    key, basis = _key(job, payload, focus), _basis(root, job)
    sources = {}
    for ref in job['history']['nodes']:
        source = history._source(root, ref['step'])
        if source[3] != ref['seal_sha256']:
            raise ValueError('Decision request scientific prefix identity changed')
        sources[ref['step']] = source
    for original, prior, seal, _ in sources.values():
        control = seal.get('control_outcome', {})
        if (original['role'] != 'A' or original.get('purpose') in {'closeout','formal_review'}
                or prior.get('next_action') not in ACTIONS
                or control.get('decision_status') != 'applied'
                or control.get('search_status') == 'unresolved'
                or control.get('selection_status') == 'unresolved'):
            continue
        if (prior.get('decision_request') is not None and _completed(original, prior, sources)
                and _key(original, prior, control.get('resolved_focus')) == key and basis <= _basis(root, original)):
            return [dict(code='a_decision_request_repeated', subject=payload['next_action'])]
    return []


def reason(root, job, payload):
    from . import search
    interpretation=search.interpret(root,job,payload) if c.settings(root, job).get('search_policy') else None
    diagnostics = assess(root, job, payload,focus=interpretation['focus'] if interpretation else None)
    return diagnostics[0]['code'] if diagnostics else None
