"""Short scientific role advice, frozen as literal text when preparing a task."""
MAX_BYTES = 2560
WORK_UNITS = {
    'screen': 'Compare mechanisms relevant to the present uncertainty using fair controls. Reuse adequate evidence; report observations and obstacles. Calls alone do not establish screening.',
    'improve': 'Sustained exploration of the focused scientific mechanism toward unmet goals; adapt from observations/residuals, beyond a batch or weak result.',
    'validate': 'Resolve the assigned decision-changing gap with fair controls; stop when resolved or blocked, without a new search or unlimited proof.',
    'repair': 'Repair the exact scientific observation under its declared mechanism and original allowance. Preserve checks/accepted evidence; reconcile unknown calls, never replay.',
}

DEFAULT = {
    'H': 'Propose revisable competing explanations and distinguishing experiments; initial hypotheses must be high-quality. Preserve original constraints and counts; separate hypotheses from requirements. W may add or revise mechanisms; consult H when useful. Request E only for a bounded evidence gap.',
    'W': 'Own the research direction, mechanisms, work and candidates. Honor checks and frozen --mechanism N. Handoff freezes new/revised mechanisms without A. Reconcile unknown calls; never replay. No auxiliary agents.',
    'A': 'Independently judge premature convergence, important alternatives and decision-level progress. Own final scientific/delivery decisions; retain missing evidence/limits. W owns sustained research, methods and candidates; no detail audit.',
    'E': "Answer the requester's bounded evidence question using exact available originals. Return the scientific finding and its limitations to the requester. Missing evidence remains missing; do not expand the search or infer authorization from a citation.",
}

# A new frozen strategy chooses these literals explicitly. Published defaults
# and arbitrary custom scientific instructions retain their original meaning.
OPPORTUNITY = {
    **DEFAULT,
    'H': DEFAULT['H'],
    'W': DEFAULT['W'],
    'A': DEFAULT['A'],
}


# Versions 1 and 2 share these frozen scientific role literals. Their distinct
# readiness authority lives in purpose contracts; unmarked originals keep theirs.
WORKER_LED = {
    'H': 'Open distinct explanations, representations or problem decompositions rooted in the objective, evidence and gaps. Label speculation and distinguish it from observations. Keep original constraints fixed. Offer useful distinctions, not mandatory method-family coverage, recipes or downstream caps. W chooses the research direction and may revise it; answer consultation then return to W. Request E only for a bounded evidence gap.',
    'W': 'Own research, objective/score/validation-object alignment and runnable incumbents. Interpret observations/residuals/negative results; adapt. Consult H/A freely. Plateau/time alone is not failure. Separate exploratory/adaptive evidence from independent final checks. No exhaustive search or invented observations. Honor --mechanism N; reconcile unknown calls, never replay. No auxiliary agents.',
    'A': 'Independently check local lock-in, target drift, ignored evidence and repetitive or unreasonable search; distinguish productive deepening from stagnation. Give evidence and the key concern, or say no material issue and return to W. No method prescriptions, checklists or final-proof demands for exploratory work. Plateau/time alone is not scientific failure. Preserve missing evidence/limits and formal review independence.',
    'E': DEFAULT['E'],
}


WORKER_LED_PLANNING = {
    'H': 'Open useful explanations, representations or problem decompositions from the objective, evidence and gaps; label speculation. No mandatory method-family coverage, recipes or downstream caps. W owns direction and allocation.',
}


# New preparations choose v3 explicitly; older literal dictionaries stay frozen.
WORKER_FIRST = {
    'H': 'Answer W\'s concrete question from relevant originals, candidates and unresolved gaps. Offer alternative mechanisms, representations or decompositions and cheap observations that could distinguish them. Mark speculation. Say when no useful alternative is found; return to W. Preserve original constraints; no execution grant.',
    'W': 'Start and own research. Bind mechanisms/experiment_question via --research JSON. At results optionally update research_memory; limited negatives do not reject whole mechanisms. Separate exploratory candidates, lineage and results; adaptive evidence from final checks. W chooses methods; never replay unknown calls. No auxiliary agents.',
    'A': 'Use W\'s question, clues/candidate memory and exact evidence. Ordinary A checks omissions, misjudgment or low information value; endorse continuation or say no new issue. Keep negative claims scoped. New work needs a decision-changing gap. Final A checks delivery, reproducibility and limits; reject all or request one permitted bounded correction. Preserve formal independence and qualification.',
    'E': DEFAULT['E'],
}


# Current launcher advice is separate from the immutable scientific literal.
# This plans work only; it never changes H scope or execution admission.
STAGE_PLANNING = {
    'H': 'H explores revisable high-level scientific methods/directions; offer competing explanations and distinguishing experiments. W owns direction/design/implementation/adaptation and may add/revise mechanisms. Use exact observations/counterevidence; revisit breadth when contradictions or stalled progress justify it. Keep original task constraints/counts/commitments fixed, distinct from hypotheses. Consultation answers W then returns to a fresh W. Publish rationale in declared outputs; cite managed originals. No downstream budget/call/audit caps.',
    'A': 'Judge premature convergence, important alternatives and decision progress; W owns methods and allocation. Reuse exact originals/limits.',
    'W': 'W sustains direction/design/adaptation via observations/residuals;H:hypotheses;A:decision-changing judgment. Conflict/stall:revisit breadth. Keep runnable candidates/helpers,negatives,exact originals.',
}


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
    """One model-facing purpose contract, frozen with each new task."""
    role,scope=job['role'],purpose(job)
    if job.get('research_strategy_version') in {2,3}:
        if scope=='delivery_review':
            return ('Independently judge readiness before final lock. Availability is not scientific qualification; '
                'reject all if needed. Exact deliver authorizes; best-effort needs original task policy. Follow action options.')
        if scope=='delivery_correction':
            if job.get('research_strategy_version')==3:
                return 'Resolve only the exact requested gap within original remaining budgets and frozen --mechanism N; no new declarations, broader research or consultation. Return for one reassessment.'
            return 'Resolve only the exact requested decision-changing gap within original remaining budgets; no broader research or consultation. Return for one reassessment.'
        if role=='A' and scope=='protected_closeout':
            return 'Judge retained evidence only; reject all if needed despite available candidates. Exact deliver authorizes; best-effort needs original task policy. No new work.'
    if job.get('research_strategy_version') in {1,2,3}:
        if role=='H':
            if job.get('research_strategy_version')==3:
                return ('Use W\'s question and linked originals; return alternatives or no useful addition to W. No compulsory initial H stage.'
                    +(' Advice without a new plan: next_action=wait returns to W.' if scope=='worker_consultation' else ''))
            return ('Answer W\'s hypothesis question, then return to W. ' if scope=='worker_consultation' else '')+WORKER_LED_PLANNING['H']
        if role=='W':
            # Stage routes carry W's purpose and authority once, alongside the
            # actual authored output fields rather than a second role banner.
            return ''
        if role=='A' and scope in {'assessment','worker_consultation'}:
            route=''
            if scope=='worker_consultation':
                route+='Answer W\'s concrete question with a meaningful judgment, then return to W; no global allocation, candidate choice, formal verdict or grant. '
            else:
                route+=('No material issue: continue_w with decision_request=null. '
                    'Extra work requires concrete bounded decision-changing decision_request{decision:candidate|mechanism|compliance,question,change_criterion}; W chooses methods. '
                    'deliver/finish requests mandatory final A assessment before delivery, not authorization. ')
                if job.get('supervision_request') is not None:
                    if (job.get('research_strategy_version')==3
                            and job['supervision_request'].get('review_kind')=='ordinary_reflection'):
                        route+='Reflect on W question/clues/evidence; no new issue is valid. '
                    route+=('supervision_result: continue_existing=continue_w without decision_request; request_evidence=continue_w/revise_h/extract with decision_request; finalize=finish/deliver requests final assessment. ')
            return route+ASSESSMENT_SCOPE
    if role=='A':
        if scope=='formal_review':
            route='Judge the exact independent subject with the requested verdict; do not edit it. '
        elif scope=='worker_consultation':
            route='Answer W\'s concrete question with a meaningful judgment, then return to W; no global allocation, candidate choice, formal verdict or grant. '
        elif scope in {'closeout','protected_closeout'}:
            route='Judge retained candidates/claims; follow closeout action options; no new exploration. '
        elif job.get('supervision_request') is not None:
            route=('Judge premature convergence, important alternatives and decision progress at the frozen cutoff. '
                'supervision_result: continue_existing=continue_w without decision_request, retaining W focus/unfinished work; '
                'request_evidence=continue_w/revise_h/extract with decision_request{decision:candidate|mechanism|compliance,question,change_criterion}; '
                'W chooses methods; H reconstructs mechanisms. finalize=finish/deliver. A owns final scientific/delivery decisions. ')
        else:
            route='Answer the concrete assessment question with a meaningful judgment; choose deliver/finish/continue_w or a bounded evidence route, not global scheduling. Further work needs decision_request{decision:candidate|mechanism|compliance,question,change_criterion}: a concrete gap and result that can change this decision. '
        return route+ASSESSMENT_SCOPE
    if role=='H' and scope=='worker_consultation':
        return 'Answer the exact W hypothesis question; revise explanations/search_plan when useful, then return to W. No formal verdict or execution grant.'
    if role=='W':
        return 'Batches/progress/windows alone do not require A; ask a concrete decision-changing question.'
    return ''



def policy(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Guidance requires exact H/W/A/E scientific text')
    if any(not isinstance(text, str) or not text.strip() for text in value.values()):
        raise ValueError('Role guidance must contain scientific text')
    if sum(len(text.encode()) for text in value.values()) > MAX_BYTES:
        raise ValueError('All scientific role guidance exceeds its frozen byte bound')
    return {role: value[role] for role in DEFAULT}
