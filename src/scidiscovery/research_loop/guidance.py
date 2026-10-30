"""Short scientific role advice, frozen as literal text when preparing a task."""
MAX_BYTES = 2560
WORK_UNITS = {
    'screen': 'Compare assigned mechanisms cheaply; report observations or obstacles. Calls alone do not establish screening.',
    'improve': 'Search within H scientific scope; stop for A when gains stall, scope changes or original limits require review. Further fragments retain consumed budgets.',
    'validate': 'Resolve the assigned decision-changing gap with fair controls; stop when resolved or blocked, without a new search or unlimited proof.',
    'repair': 'Repair the exact failed observation within H and the original allowance. Preserve checks/accepted evidence; reconcile unknown calls, never replay.',
}

DEFAULT = {
    'H': 'Choose screen for a cheap mechanism comparison; improve/repair/validate name one mechanism. Compare plausible explanations before concentrating; justify a single line. Each minimal_test states differing predictions, the discriminating signal, priority and reopening condition. Where useful, compare 2-3 mechanisms with the same data/evaluation in one W; this is no quota. W owns methods/grids. Preserve user/task constraints and commitments; request E as needed.',
    'W': 'Own methods/candidates within H; A suggests methods. Honor checks; use --mechanism. Initial/grant calls protect screening opportunities, not trial totals. Route scope changes via A to H; reconcile unknown calls, never replay. Return to independent A, not global completion. No auxiliary agents.',
    'A': "A owns goal/evaluation-version judgment and counterevidence. No incumbent/unknown-optimality veto. continue_w=one finite stage; calls/audits aren't goal proof.",
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
    'H': 'Own questions/mechanisms/evidence and reevaluation; no downstream token/time budgets or execution/call/audit caps. Control config/live allowances govern. Retain scientific counts, user/task requirements and native per-call caps.',
    'A': 'Initial/grant=first-screen fairness, not total calls; zero alone is no denial/H trigger. continue_w for scoped science; revise_h for question/mechanism/constraint changes. No successor budgets/caps; H/A suggestions are not control limits. Control config/live allowances govern.',
    'W': 'Implement/validate incrementally within scientific scope/control allowance; size next work from evidence/current budget. H/A estimates add no runtime caps. Return to A for scientific stall, scope change or actual runtime refusal.',
}



def policy(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Guidance requires exact H/W/A/E scientific text')
    if any(not isinstance(text, str) or not text.strip() for text in value.values()):
        raise ValueError('Role guidance must contain scientific text')
    if sum(len(text.encode()) for text in value.values()) > MAX_BYTES:
        raise ValueError('All scientific role guidance exceeds its frozen byte bound')
    return {role: value[role] for role in DEFAULT}
