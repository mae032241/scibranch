"""Scientific A requests and exact completed-request replay; no value classifier."""
from pathlib import Path

from . import accepted_budget,accepted_prefix,accepted_search,contracts as c,correction,history

ACTIONS = {'continue_w': 'W', 'revise_h': 'H', 'extract': 'E'}


def schema():
    fields = dict(decision=dict(type='string', enum=['candidate', 'mechanism', 'compliance']),
                  question=dict(type='string'),
                  change_criterion=dict(type='string'))
    return dict(type=['object', 'null'], properties=fields, required=list(fields), additionalProperties=False,
        description='Optional context for continue_w, revise_h or extract: the scientific decision, concrete question '
                    'and what result would change it. Missing or unconsumable context uses the actual route and reason. '
                    'No evidence paths or hashes: the host binds the actual mechanism and assessed originals.')


def _request(job,payload):
    request=c.scientific_field(job,payload,'decision_request')
    return request if request is not None and all(request[key].strip() for key in ('question','change_criterion')) else None


def _key(job,payload,focus):
    request=_request(job,payload)
    mechanism=focus.get('mechanism_ref') if focus else None
    if request is None:
        return ('route',payload['next_action'],payload['route_reason'].strip(),mechanism,payload.get('candidate_recommendation'))
    candidate=payload.get('candidate_recommendation') if request['decision']=='candidate' else None
    return (payload['next_action'],request['decision'],request['question'].strip(),request['change_criterion'].strip(),mechanism,candidate)


def _basis(root,job,*,sealed=False):
    if job.get('author_correction'):
        original=history._source(root,job['author_correction']['root']['step'])[0]
        return correction._proofs(root,original,{'verdict':'progress'},sealed=True)
    return correction._proofs(root,job,{'verdict':'progress'},sealed=sealed)


def _requests(root,job,action):
    """Compare only relevant A originals in this declared accepted cut."""
    prefix=accepted_prefix.for_job(job)
    directory=Path(root)/accepted_prefix.MEMBERS/accepted_prefix._view_key(prefix)/'ordinals'
    head=accepted_budget.read(root)
    for ordinal in range(1,prefix['count']+1):
        link=c.read(c.regular(directory/f'{ordinal:09d}.json'))
        metadata=accepted_budget.search_row(root,accepted_search.key('node',link['step']),head=head)
        if metadata is None or metadata['origin_identity']!=link['origin_identity']:
            raise ValueError('Decision request scientific prefix identity changed')
        if metadata['role']!='A' or metadata.get('next_action')!=action:continue
        member=accepted_prefix.member(root,prefix,link['step'],origin=link['origin_identity'])
        row=history._source(root,link['step'])
        if row[3]!=member['seal_ref']:raise ValueError('Decision request scientific prefix identity changed')
        yield row


def _response(root,prefix,job,payload):
    step=job['step']+1
    if step>prefix['cutoff']:return False
    try:member=accepted_prefix.member(root,prefix,step)
    except FileNotFoundError:return False
    response=history._source(root,step)
    if response[3]!=member['seal_ref']:
        raise ValueError('Decision response scientific prefix identity changed')
    return _completed(job,payload,{step:response})


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
    if c.settings(root,job).get('decision_contract_version')!=1 or job['role']!='A' or job.get('purpose') in {'closeout','formal_review'}:
        return []
    if payload['next_action'] not in ACTIONS:return []
    key,basis=_key(job,payload,focus),_basis(root,job)
    prefix=accepted_prefix.for_job(job)
    for original,prior,seal,_ in _requests(root,job,payload['next_action']):
        control=seal.get('control_outcome',{})
        if (original['role']!='A' or original.get('purpose') in {'closeout','formal_review'}
                or prior.get('next_action') not in ACTIONS or control.get('decision_status')!='applied'
                or control.get('search_status')=='unresolved' or control.get('selection_status')=='unresolved'):
            continue
        if (_key(original,prior,control.get('resolved_focus'))==key and _response(root,prefix,original,prior)
                and basis<=_basis(root,original,sealed=True)):
            return [dict(code='a_decision_request_repeated',subject=payload['next_action'])]
    return []


def reason(root, job, payload):
    from . import originals,search
    seal=originals.source_record(originals.node(root,job['step']),'result-identity.json')
    if seal.exists():
        # Routing consumes the accepted answer, including after newer history
        # or a caller-supplied payload has changed the prospective comparison.
        control=c.read(c.regular(seal))['control_outcome']
        if control['decision_status']!='unresolved':return None
        return next((row['code'] for row in control['diagnostics']
            if row['code']=='a_decision_request_repeated'),'a_decision_request_repeated')
    focus=None
    if c.settings(root,job).get('search_policy'):
        focus=search.interpret(root,job,payload)['focus']
    diagnostics=assess(root,job,payload,focus=focus)
    return diagnostics[0]['code'] if diagnostics else None
