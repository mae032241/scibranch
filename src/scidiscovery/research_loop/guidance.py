"""Short scientific role advice, frozen as literal text when preparing a task."""
MAX_BYTES = 2560
WORK_UNITS = {
    'screen': 'Compare mechanisms relevant to the present uncertainty using fair controls. Reuse adequate evidence; report observations and obstacles. Calls alone do not establish screening.',
    'improve': 'Sustained exploration of the focused scientific mechanism toward unmet goals; adapt from observations/residuals, beyond a batch or weak result.',
    'validate': 'Resolve the assigned decision-changing gap with fair controls; stop when resolved or blocked, without a new search or unlimited proof.',
    'repair': 'Repair the exact scientific observation under its declared mechanism and original allowance. Preserve checks/accepted evidence; reconcile unknown calls, never replay.',
}

# Current scientific advice is frozen once; per-task subagent/E guidance lives in role_contract.
ADVISORY_WORKER_FIRST = {
    'H': 'Answer W\'s concrete question from relevant originals, candidates and unresolved gaps. Offer alternative mechanisms, representations or decompositions and cheap observations that could distinguish them. Mark speculation. Say when no useful alternative is found; return to W. Preserve original constraints; no execution grant.',
    'W': 'Own research/methods; bind mechanisms/experiment_question via --research JSON. Scope negatives; separate candidates/lineage/results and adaptive/final evidence. No unknown-call replay; judge H/A advice against evidence.',
    'A': 'Diagnose evidence-backed local lock-in, ignored counterevidence or critical alternatives, goal drift, or serious logic errors. Counts, low scores and elapsed time alone prove none. Ordinary A gives fallible advice to W, without choosing models, depth, folds, bagging, search order or banning broader exploration. W judges the concern and chooses self-correction, H, E, or evidence-based disagreement. No material issue means no new constraints. Final A separately assesses delivery and limits.',
    'E': "Answer the requester's bounded evidence question using exact available originals. Return the scientific finding and its limitations to the requester. Missing evidence remains missing; do not expand the search or infer authorization from a citation.",
}


DEFAULT = ADVISORY_WORKER_FIRST


def advisory_assessment(job):
    """The frozen ordinary-advice contract excludes protected/final scopes."""
    return (job.get('advisory_scope_version') == 1 and job.get('research_strategy_version') == 3
        and job.get('role') == 'A' and purpose(job) in {'assessment','worker_consultation'}
        and not any(job.get(key) for key in ('engineering_repair','author_correction','correction_request')))


ASSESSMENT_SCOPE = (
    'High-level decision-changing evidence/claims/delivery only; routine formatting, file identity, interfaces and metric checks belong to mechanical checks/W. '
    'Retain exact originals/qualification/limits. Missing hidden scores, a unique generator or perfect certainty are not indefinite vetoes.')


def purpose(job):
    """Interpret existing task flags only; no scheduling or new control state."""
    if job.get('formal_review') or job.get('purpose')=='formal_review':return 'formal_review'
    if job.get('purpose')=='worker_consultation':return 'worker_consultation'
    if job.get('final_only') or job.get('final_report'):return 'protected_closeout'
    from . import delivery_review
    review_scope=delivery_review.scope(job)
    if review_scope:return review_scope
    if job.get('purpose')=='closeout':return 'closeout'
    return 'assessment' if job['role']=='A' else 'research'


def role_contract(job):
    """One current model-facing purpose contract, frozen with each new task."""
    role,scope=job['role'],purpose(job)
    if (role=='W' and scope=='research'
            and not any(job.get(key) for key in ('engineering_repair','author_correction','correction_request'))):
        if job.get('native_subagents') is True:
            return 'Subagents enabled: prefer small extraction/scripts. W chooses E for large/complex independent evidence.'
        if job.get('native_subagents') is False:
            return 'Subagents disabled: do small tasks yourself. W chooses E for large/complex independent evidence.'
        return 'Supported/permitted subagents only; else do small tasks yourself. W chooses E for large/complex independent evidence.'
    if advisory_assessment(job):
        return ('Diagnose only evidence-backed lock-in, ignored counterevidence/critical alternatives, goal drift or serious logic errors. '
            'No material issue means no new constraints; counts, low scores, elapsed time and engineering failures alone prove none. Distinguish productive deepening from repeated investment without new distinguishing evidence. Do not select methods, depth, folds, bagging or search order, or ban broader exploration. '
            'W judges advice and chooses self-fix, H for alternatives, E for missing evidence, or evidence-based disagreement. '
            'Return continue_w with decision_request=null; advice stays in scientific_result, never a binding work order or finalization request. '
            'Final A remains a separate pre-delivery assessment; preserve original permissions, qualification and formal independence.')
    if scope=='delivery_review':
        return ('Independently judge readiness before final lock. Availability is not scientific qualification; '
            'reject all if needed. Exact deliver authorizes; best-effort needs original task policy. Follow action options.')
    if scope=='delivery_correction':
        return 'Resolve only the exact requested gap within original remaining budgets and frozen --mechanism N; no new declarations, broader research or consultation. Return for one reassessment.'
    if role=='A' and scope=='protected_closeout':
        return 'Judge retained evidence only; reject all if needed despite available candidates. Exact deliver authorizes; best-effort needs original task policy. No new work.'
    if role=='H':
        return ('Use W\'s question and linked originals; return alternatives or no useful addition to W. No compulsory initial H stage.'
            +(' Advice without a new plan: next_action=wait returns to W.' if scope=='worker_consultation' else ''))
    if role=='W':
        return ''
    if role=='A':
        if scope=='formal_review':
            route='Judge the exact independent subject with the requested verdict; do not edit it. '
        elif scope=='worker_consultation':
            route='Answer W\'s concrete question with a meaningful judgment, then return to W; no global allocation, candidate choice, formal verdict or grant. '
        elif scope=='closeout':
            route='Judge retained candidates/claims; follow closeout action options; no new exploration. '
        else:
            route=('No material issue: continue_w with decision_request=null. '
                'Extra work requires concrete bounded decision-changing decision_request{decision:candidate|mechanism|compliance,question,change_criterion}; W chooses methods. '
                'deliver/finish requests mandatory final A assessment before delivery, not authorization. ')
            if job.get('supervision_request') is not None:
                if job['supervision_request'].get('review_kind')=='ordinary_reflection':
                    route+='Reflect on W question/clues/evidence; no new issue is valid. '
                route+=('supervision_result: continue_existing=continue_w without decision_request; request_evidence=continue_w/revise_h/extract with decision_request; finalize=finish/deliver requests final assessment. ')
        return route+ASSESSMENT_SCOPE
    return ''


def policy(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Guidance requires exact H/W/A/E scientific text')
    if any(not isinstance(text, str) or not text.strip() for text in value.values()):
        raise ValueError('Role guidance must contain scientific text')
    if sum(len(text.encode()) for text in value.values()) > MAX_BYTES:
        raise ValueError('All scientific role guidance exceeds its frozen byte bound')
    return {role: value[role] for role in DEFAULT}


def continue_wait():
    return ('The operator explicitly requested continuation from the retained wait. Reassess the original goal, '
        'evidence and remaining limitations before choosing the next scientific route.')


def consultation_return(requester_step, advice_step):
    return (f'Resume W research toward the original objective using retained originals in inputs/parents/{requester_step:03d}/conclusion.json '
        f'and the fallible consultation in inputs/parents/{advice_step:03d}/conclusion.json. '
        'Judge the concern against evidence; choose self-correction, H for alternatives, E for missing evidence, or evidence-based disagreement. '
        'No material issue adds no constraints. W owns direction, methods and allocation; original permissions, materials and consumed budgets persist.')


def delivery_correction(step, request, instruction):
    return (f'Resolve only the decision-changing gap from A in inputs/parents/{step:03d}/conclusion.json. '
        'Question: '+request['question']+' Change criterion: '+request['change_criterion']+'. '
        'Choose your methods within the original remaining authorization. '+instruction)


def delivery_reassessment(instruction):
    return 'Reassess the single corrected delivery gap from exact retained originals. No further correction is authorized. '+instruction


def pre_delivery_assessment(instruction, *, ordinary_request=False):
    if ordinary_request:
        return ('Independent pre-delivery assessment of the ordinary assessment request. Accept an exact candidate with deliver, '
            'decline with finish, or request one bounded decision-changing correction with continue_w and decision_request. '+instruction)
    return ('Independent pre-delivery assessment: accept an exact existing candidate with deliver, decline with finish, '
        'or request one bounded decision-changing correction with continue_w and a concrete decision_request. '
        'No new authorization or budget is granted. '+instruction)


def worker_assessment(step, instruction, *, ending, control_outcome):
    source=f'inputs/parents/{step:03d}/conclusion.json'
    request=(' Authored W request: '+instruction) if instruction else ''
    question=(f'Answer the W request in {source}.{request} '
        'Assess core scientific direction, decision-changing evidence and delivery/completion claims at a high level. '
        'Use the exact retained originals and stated limitations; this handoff grants no qualification or execution permission.')
    if ending:
        question+=' Control ended this stage for '+ending['reason']+'; this ending does not establish scientific completion.'
    delivery=control_outcome.get('worker_delivery') or {}
    if delivery.get('status')=='partial':
        question+=' Retained work has partial coverage ('+delivery['reason']+').'
        if delivery.get('missing'):
            question+=' Missing assigned observations: '+', '.join(delivery['missing'])+'.'
        if delivery.get('blocked'):
            question+=' Authored blocked mechanisms: '+', '.join(delivery['blocked'])+'.'
    elif not ending and control_outcome.get('stage_end_reason')=='continuation_unavailable':
        question+=' Frozen context continuation was unavailable; this ending does not establish scientific completion.'
    return question


def worker_handoff(step, instruction):
    request=(' Authored W request: '+instruction) if instruction else ''
    return (f'Continue the retained W research in inputs/parents/{step:03d}/conclusion.json.{request} '
        'Preserve original directions, unfinished questions, candidates, materials, mechanisms and consumed opportunities. '
        'Partial sealing is neither independent A review nor mechanism completion; permissions and global budgets persist.')


def worker_consultation(step, instruction, *, question, intent, same_session):
    request=(' Authored W request: '+instruction) if instruction else ''
    return (f'Answer the requesting W consultation in inputs/parents/{step:03d}/conclusion.json.{request} '
        +('Requested information: '+question+' ' if question else '')
        +'Use its exact question, retained evidence and limitations. '
        +('Diagnose only evidence-backed lock-in, ignored counterevidence or critical alternatives, goal drift or serious logic errors; W judges fallible advice. ' if intent=='review' else '')
        +('Return advice to the requesting W work; no formal verdict, global decision or execution grant.' if same_session else
            'Return advice for a new W; no formal verdict, global decision or execution grant.'))


def advisory_return(step):
    return (f'Resume W research toward the original objective using retained originals and the fallible advice in inputs/parents/{step:03d}/conclusion.json. '
        'W judges any concern against evidence and chooses self-correction, H for alternatives, E for missing evidence, or evidence-based disagreement. '
        'No material issue adds no constraints. W owns direction, methods and allocation; original permissions, materials and consumed budgets persist.')


def extraction_return(step, requester_step):
    return (f'Evaluate the returned extraction in inputs/parents/{step:03d}/conclusion.json '
        f'against your request in inputs/parents/{requester_step:03d}/conclusion.json. '
        'Account for missing evidence and limitations, then choose the next scientific route.')


def direction_reflection():
    return ('Review the exact retained W question, clues and evidence for evidence-backed lock-in, ignored counterevidence or critical alternatives, goal drift or serious logic errors. '
        'Counts, low scores and consumption only trigger this opportunity; they establish no scientific failure. '
        'Give fallible advice or no material issue, then return to W; no method prescriptions, exploration bans, binding new work or finalization. '
        'W decides self-correction, H, E, or evidence-based disagreement. Original objective and permissions persist.')


def unavailable_advice(requester_step, *, role, advice_step):
    return (f'Resume the original W work in inputs/parents/{requester_step:03d}/conclusion.json. '
        f'The optional {role} consultation at step {advice_step} was unavailable after bounded native recovery; '
        'it supplied no scientific conclusion or advice. Continue from retained W evidence and unfinished questions. '
        'W owns direction, methods and allocation; original permissions, materials and consumed budgets persist.')


def correction_reconsideration(reason, step):
    return (f'\nControl requires H to reconsider {reason} after sealed A {step}; '
        'own the scientific response; the prior A conclusion is unchanged.')


def decision_clarification(reason):
    return ('Clarify the scientific decision, concrete question and result that would change it. '
        'The original science is retained; no dependent research is authorized: '+reason+'.')


def author_control_correction(reason):
    return ('Correct this unresolved scientific control choice: '+reason+'. '
        'The original science is retained; no dependent experiment is authorized. '
        'Declare only the needed scientific change, or choose a justified alternative route.')


def optional_route_reconsideration(reason):
    return ('Control refused the optional scientific node: '+reason+'. '
        'Reconsider the scientific route from the retained evidence. Choose a justified alternative; '
        'no new evidence, budget, execution permission or renewed correction allowance is supplied.')
