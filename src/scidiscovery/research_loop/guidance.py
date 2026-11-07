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


# Current launcher advice is separate from the immutable scientific literal.
# This plans work only; it never changes H scope or execution admission.
STAGE_PLANNING = {
    'H': 'H explores revisable high-level scientific methods/directions; offer competing explanations and distinguishing experiments. W owns direction/design/implementation/adaptation and may add/revise mechanisms. Use exact observations/counterevidence; revisit breadth when contradictions or stalled progress justify it. Keep original task constraints/counts/commitments fixed, distinct from hypotheses. Consultation answers W then returns to a fresh W. Publish rationale in declared outputs; cite managed originals. No downstream budget/call/audit caps.',
    'A': 'Judge premature convergence, important alternatives and decision progress; W owns methods and allocation. Reuse exact originals/limits.',
    'W': 'W owns sustained research direction/design/adaptation from observations and residuals. H for hypotheses; A for a decision-changing judgment. Revisit breadth for conflict/stall. Keep runnable candidates/helpers, negatives and exact originals.',
}


ASSESSMENT_SCOPE = (
    'High-level decision-changing evidence/claims/delivery only; routine formatting, file identity, interfaces and metric checks belong to mechanical checks/W. '
    'Retain exact originals/qualification/limits. Missing hidden scores, a unique generator or perfect certainty are not indefinite vetoes.')


def purpose(job):
    """Interpret existing task flags only; no scheduling or new control state."""
    if job.get('formal_review') or job.get('purpose')=='formal_review':return 'formal_review'
    if job.get('purpose')=='worker_consultation':return 'worker_consultation'
    if job.get('final_only') or job.get('final_report'):return 'protected_closeout'
    if job.get('purpose')=='closeout':return 'closeout'
    return 'assessment' if job['role']=='A' else 'research'


def role_contract(job):
    """One model-facing purpose contract, frozen with each new task."""
    role,scope=job['role'],purpose(job)
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
