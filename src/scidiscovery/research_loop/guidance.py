"""Short scientific role advice, frozen as literal text when preparing a task."""
MAX_BYTES = 2560
WORK_UNITS = {
    'screen': 'Screen the assigned H combination with cheap discriminating observations and your own findings or concrete obstacles. Preserve declared checks. A completed call alone is not scientific screening; report the combination when its assigned coverage is ready for A.',
    'improve': 'Run a bounded, observation-driven search within the assigned H mechanism and A conditions. A candidate or local gain is intermediate; choose useful next trials without proving every candidate. Finish when the search contribution is adequate, gains stall or macro judgment is needed. Preserve declared checks; eligible further fragments use stage_continuation and the original budget. Raise unknown work, scope changes or host boundaries without replay.',
    'validate': 'Resolve the assigned evidence gap for a concrete scientific decision using the declared checks and fair controls. Preserve user and H requirements. This is targeted validation, not a new candidate search or unlimited proof of an unknown optimum; finish when the gap is resolved or a scientific obstacle needs A.',
    'repair': 'Restore a trustworthy observation from the exact assigned failure within H scope and the original repair allowance. Preserve declared checks and reuse accepted originals. Never replay unknown work; report restored evidence or the remaining obstacle for A.',
}
DEFAULT = {
    'H': 'Choose screen for a cheap mechanism comparison; improve/repair/validate name one mechanism. Compare plausible explanations before concentrating; justify a single line. Each minimal_test states differing predictions, the discriminating signal, priority and reopening condition. Where useful, compare 2-3 mechanisms with the same data/evaluation in one W; this is no quota. W owns methods/grids. Preserve constraints, limits and omitted commitments; request E as needed.',
    'W': 'Own methods/candidates in frozen H scope; label managed runs with --mechanism. A methods are suggestions. improve permits observation-driven local search; validate informs a concrete choice with fair controls. Neither means one candidate or full validation per candidate. Honor required checks. screen gives scientific findings or blocked reasons for every assigned mechanism; blocked/decision is partial. Initial/grant calls cover breadth fairness, not local trial totals; original runtime/feedback limits apply. Route mechanism changes via A to H; ask A beyond W authority. Query unknown calls without replay. No auxiliary agents.',
    'A': 'Compare candidates by the same original-goal criteria and exact evaluation versions: gains, losses, unknowns. Existing candidates have no priority; unknown optimality alone cannot block replacement. Honor required checks. After completed W, fill portfolio_review: current main line, strongest feasible competing explanation with exact scientific references, cheapest choice-changing work and continue/switch/reframe/close reason. Explain infeasible alternatives/work without inventing them. Read all active work pages; explicitly execute, defer with trigger/revisit, or withdraw with scientific evidence. Execution alone does not answer a question. Consider same-data contrasts; W owns methods; use H for scope changes and E for evidence. FINAL/finish/deliver needs final_coverage for every listed mechanism, including waived: authored depth/reason, exact science_refs, unanswered questions and limitations. Counts and candidates are not depth. Audit qualification is not target evidence. continue_w requests one finite stage; usage remains consumed.',
    'E': "Answer the requester's bounded evidence question using exact available originals. Return the scientific finding and its limitations to the requester. Missing evidence remains missing; do not expand the search or infer authorization from a citation.",
}

# A new frozen strategy chooses these literals explicitly. Published defaults
# and arbitrary custom scientific instructions retain their original meaning.
OPPORTUNITY = {
    **DEFAULT,
    'H': DEFAULT['H'],
    'W': 'Own methods/candidates in frozen H scope; label managed runs with --mechanism. A methods are suggestions. improve permits observation-driven local search; validate informs a concrete choice with fair controls. Neither means one candidate or full validation per candidate. Honor required checks. screen gives scientific findings or blocked reasons for every assigned mechanism; blocked/decision is partial. Initial/grant calls cover breadth fairness, not local trial totals; original runtime/feedback limits apply. Route mechanism changes via A to H; ask A beyond W authority. Query unknown calls without replay. No auxiliary agents.',
    'A': DEFAULT['A'],
}



def policy(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Guidance requires exact H/W/A/E scientific text')
    if any(not isinstance(text, str) or not text.strip() for text in value.values()):
        raise ValueError('Role guidance must contain scientific text')
    if sum(len(text.encode()) for text in value.values()) > MAX_BYTES:
        raise ValueError('All scientific role guidance exceeds its frozen byte bound')
    return {role: value[role] for role in DEFAULT}
