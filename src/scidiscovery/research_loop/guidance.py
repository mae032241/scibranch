"""Short scientific role advice, frozen as literal text when preparing a task."""
MAX_BYTES = 2560
WORK_UNITS = {
    'screen': 'Compare assigned mechanisms cheaply; report observations or obstacles. Calls alone do not establish screening.',
    'improve': 'In H, follow findings/unmet goals beyond recipes; return to A for independent strategy/judgment, direction-changing evidence, stalls, scope changes, blockers or limits. Retain spent budgets.',
    'validate': 'Resolve the assigned decision-changing gap with fair controls; stop when resolved or blocked, without a new search or unlimited proof.',
    'repair': 'Repair the exact failed observation within H and the original allowance. Preserve checks/accepted evidence; reconcile unknown calls, never replay.',
}

DEFAULT = {
    'H': 'Choose screen for mechanism comparison; improve/repair/validate name one mechanism. Each minimal_test states differing predictions, signal, priority and reopening condition. Preserve user/task constraints, required counts and commitments; request E as needed.',
    'W': 'Own methods/candidates within H; A proposes methods. Honor checks; use --mechanism=task catalog number. Initial/grant opportunities are not trial caps. Route scope changes via A to H; reconcile unknown calls, never replay. Return to independent A, not global completion. No auxiliary agents.',
    'A': 'Assess progress independently.',
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
    'H': 'H owns questions, mechanisms and criteria; W owns methods and grids. Compare plausible explanations before concentrating; justify a single line. Mechanisms differ in assumptions/failure modes, not parameter variants. Use retained evidence/A gaps to revise, reopen or add mechanisms beyond screening with a minimal executable discriminator. In minimal_test set public metric/conditions, reference procedure and evaluation driver; cite exact originals for reuse. Seed/split/perturbation repeats only if useful. No downstream budget/call/audit caps.',
    'A': 'Reuse A deltas/scripts/full pool; previews do not prune rivals/procedures. Check goal/proxy/audit fit, rank flips/tuning; changed bases limit comparisons. portfolio_review: exact rival/test refs. Near end compare 2-3 full procedures if useful/affordable, no quota/score default. Hypothesis gaps/bottlenecks/changed assumptions/criteria: revise_h: minimal executable test; else continue_w. May reject all.',
    'W': 'Reuse exact driver/report refs/conditions; compare full tuning/output procedures; deliver from that procedure. Batch if useful; artifacts/ reports, work/ scratch.',
}



def policy(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Guidance requires exact H/W/A/E scientific text')
    if any(not isinstance(text, str) or not text.strip() for text in value.values()):
        raise ValueError('Role guidance must contain scientific text')
    if sum(len(text.encode()) for text in value.values()) > MAX_BYTES:
        raise ValueError('All scientific role guidance exceeds its frozen byte bound')
    return {role: value[role] for role in DEFAULT}
