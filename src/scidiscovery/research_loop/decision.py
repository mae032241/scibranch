"""Scientific A requests and exact completed-request replay; no value classifier."""
from pathlib import Path

from . import delivery_review,accepted_budget,accepted_prefix,accepted_search,contracts as c,correction,history

ACTIONS = {'continue_w': 'W', 'revise_h': 'H', 'extract': 'E'}


def schema():
    fields = dict(decision=dict(type='string', enum=['candidate', 'mechanism', 'compliance']),
                  question=dict(type='string'),
                  change_criterion=dict(type='string'))
    return dict(type=['object', 'null'], properties=fields, required=list(fields), additionalProperties=False,
        description='Required for ordinary A requesting new continue_w, revise_h or extract work: the scientific decision, concrete gap/question '
                    'and the result that would change its assessment. '
                    'No evidence paths or hashes: the host binds the actual mechanism and assessed originals.')


def _request(job,payload):
    request=c.scientific_field(job,payload,'decision_request')
    return request if request is not None and all(request[key].strip() for key in ('question','change_criterion')) else None


def validate_supervision(job,payload):
    """Validate a scoped A direction decision from supplied values, without I/O."""
    if job['role']!='A' or job.get('supervision_request') is None or (c.guidance.purpose(job)!='assessment' and not c.guidance.advisory_assessment(job)):
        return None
    result=c.scientific_field(job,payload,'supervision_result')
    action=payload.get('next_action')
    science=payload.get('scientific_result')
    meaningful=isinstance(science,str) and bool(science.strip())
    if c.guidance.advisory_assessment(job):
        if meaningful and result=='continue_existing' and action=='continue_w' and payload.get('decision_request') is None:
            return result
        from .output_repair import OutputRepairRequired
        raise OutputRepairRequired('a_supervision_result_inconsistent',
            'Return continue_w with supervision_result=continue_existing and decision_request=null. '
            'Use nonblank scientific_result for the evidence-backed concern or no material issue; W decides how to respond. No binding work or finalization request.')
    if meaningful and result=='continue_existing' and action=='continue_w' and payload.get('decision_request') is None:
        return result
    if meaningful and result=='request_evidence' and action in ACTIONS and _request(job,payload) is not None:
        return result
    if meaningful and result=='finalize' and action in {'finish','deliver'}:
        return result
    from .output_repair import OutputRepairRequired
    raise OutputRepairRequired('a_supervision_result_inconsistent',
        'Correct this same A output: supply nonblank scientific_result and supervision_result. '
        'continue_existing=continue_w with no decision_request; request_evidence=continue_w/revise_h/extract '
        'with concrete decision_request question+change_criterion; finalize=finish/deliver. '
        'Keep frozen supervision scope and original science/limits; this repair authorizes no new research.')


def require_request(job,payload):
    """Require explicit further-work intent without judging scientific prose."""
    if delivery_review.scope(job)=='delivery_review':
        science=payload.get('scientific_result')
        if not isinstance(science,str) or not science.strip() or (payload.get('next_action')=='continue_w' and _request(job,payload) is None):
            from .output_repair import OutputRepairRequired
            raise OutputRepairRequired('a_delivery_review_incomplete',
                'Supply nonblank scientific_result. continue_w requires one concrete decision_request with question and change_criterion; no additional authorization is granted.')
        return _request(job,payload) if payload.get('next_action')=='continue_w' else None
    if c.guidance.advisory_assessment(job):
        science=payload.get('scientific_result')
        if (not isinstance(science,str) or not science.strip()
                or payload.get('next_action')!='continue_w'
                or 'decision_request' not in payload or payload['decision_request'] is not None):
            from .output_repair import OutputRepairRequired
            raise OutputRepairRequired('a_advisory_scope_inconsistent',
                'Give nonblank scientific_result diagnosing a material evidence-backed reasoning concern or no material issue. '
                'Return continue_w with decision_request=null; H/E suggestions are advice for W, never dispatch or finalization. Separate engineering/formal scopes retain their existing gates.')
        validate_supervision(job,payload)
        return None
    supervision=validate_supervision(job,payload)
    ordinary=(job['role']=='A' and not job.get('formal_review') and c.guidance.purpose(job)=='assessment')
    if ordinary and job.get('research_strategy_version') in (1,2,3):
        science=payload.get('scientific_result')
        if not isinstance(science,str) or not science.strip():
            from .output_repair import OutputRepairRequired
            raise OutputRepairRequired('a_scientific_result_incomplete',
                'Correct this same A output with nonblank scientific_result explaining the decision and remaining limitations. '
                'This repair authorizes no new research.')
    if supervision in {'continue_existing','finalize'}:return None
    if _continue_existing(job,payload):return None
    if (job['role']!='A' or job.get('formal_review')
            or c.guidance.purpose(job)!='assessment'
            or payload.get('next_action') not in ACTIONS):return None
    request=_request(job,payload)
    if request is None:
        from .output_repair import OutputRepairRequired
        raise OutputRepairRequired('a_decision_request_incomplete',
            'Correct decision_request in this same A output: supply decision (candidate, mechanism or compliance), '
            'a nonblank concrete evidence gap/question, and a nonblank change_criterion stating what result would change '
            'the assessment. Preserve the original science and limitations; no new research is authorized by this repair.')
    return request


def _continue_existing(job,payload):
    return (job.get('research_strategy_version') in (1,2,3) and job['role']=='A'
        and not job.get('formal_review') and c.guidance.purpose(job)=='assessment'
        and payload.get('next_action')=='continue_w' and 'decision_request' in payload
        and payload['decision_request'] is None)


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


def _requests(root,job,action,*,head):
    """Compare only relevant A originals in this declared accepted cut."""
    prefix=accepted_prefix.for_job(job)
    directory=Path(root)/accepted_prefix.MEMBERS/accepted_prefix._view_key(prefix)/'ordinals'
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


def _response(root,prefix,job,payload,*,head):
    # The accepted node owns exact direct-parent and return identities, even
    # when consultations or handoffs separate the request from its response.
    directory=Path(root)/accepted_prefix.MEMBERS/accepted_prefix._view_key(prefix)/'ordinals'
    for ordinal in range(1,prefix['count']+1):
        link=c.read(c.regular(directory/f'{ordinal:09d}.json'))
        if link['step']<=job['step']:continue
        metadata=accepted_budget.search_row(root,accepted_search.key('node',link['step']),head=head)
        if metadata is None or metadata['origin_identity']!=link['origin_identity']:
            raise ValueError('Decision response scientific prefix identity changed')
        if (metadata['role']!=ACTIONS.get(payload.get('next_action'))
                or metadata.get('purpose')=='worker_consultation'
                or job['step'] not in metadata['direct_steps']):continue
        if metadata['role']=='E' and metadata.get('evidence_return')!=dict(step=job['step'],role='A'):continue
        return True
    return False


def _completed(job, payload, sources):
    for response in sources.values():
        following=response[0]
        if following['step']<=job['step'] or following['role']!=ACTIONS.get(payload.get('next_action')):
            continue
        if following.get('purpose')=='worker_consultation':continue
        if not any(ref['path'] == f'inputs/parents/{job["step"]:03d}/conclusion.json' for ref in following['direct_refs']):
            continue
        if following['role'] == 'E' and following.get('evidence_return') != dict(step=job['step'], role='A'):
            continue
        return True
    return False


def assess(root, job, payload, *, focus=None):
    policy=c.settings(root,job)
    if (policy.get('decision_contract_version')!=1 and job.get('research_strategy_version') not in (1,2,3)) or job['role']!='A' or job.get('purpose') in {'closeout','formal_review','worker_consultation'}:
        return []
    if delivery_review.scope(job) is not None:return []
    if payload['next_action'] not in ACTIONS:return []
    return _assess_from_facts(root,job,payload,policy=policy,
        interpretation=dict(focus=focus),head=accepted_budget.read(root))


def _assess_from_facts(root, job, payload, *, policy, interpretation, head):
    """Consume this attempt's interpretation and accepted comparison head."""
    if policy is None or interpretation is None or head is None:
        raise ValueError('Decision assessment requires its produced policy, interpretation and accepted head')
    if (policy.get('decision_contract_version')!=1 and job.get('research_strategy_version') not in (1,2,3)) or job['role']!='A' or job.get('purpose') in {'closeout','formal_review','worker_consultation'}:
        return []
    if job.get('supervision_request') is not None and c.scientific_field(job,payload,'supervision_result')=='continue_existing':return []
    if _continue_existing(job,payload):return []
    if delivery_review.scope(job) is not None:return []
    if payload['next_action'] not in ACTIONS:return []
    key,basis=_key(job,payload,interpretation['focus']),_basis(root,job)
    prefix=accepted_prefix.for_job(job)
    for original,prior,seal,_ in _requests(root,job,payload['next_action'],head=head):
        control=seal.get('control_outcome',{})
        if (original['role']!='A' or original.get('purpose') in {'closeout','formal_review','worker_consultation'}
                or prior.get('next_action') not in ACTIONS or control.get('decision_status')!='applied'
                or control.get('search_status')=='unresolved' or control.get('selection_status')=='unresolved'):
            continue
        if (_key(original,prior,control.get('resolved_focus'))==key and _response(root,prefix,original,prior,head=head)
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
