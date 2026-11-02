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
    'H': 'H owns questions/mechanisms/criteria; W owns methods/grids. Use A evidence/limits/exact refs for mechanism decisions; no default A/full-pool redo. Check concrete conflicts/gaps as needed. Compare plausible explanations; justify a single line. Mechanisms differ in assumptions/failure modes, not parameters. Revise/reopen/add mechanisms beyond screening with a minimal executable discriminator. minimal_test: public metric/conditions, reference procedure/evaluation driver; repeats only if useful. Publish reusable owned reports/results/executed scripts+needed helpers via declared outputs (usually artifacts/); managed originals: refs only, no copies/links. No downstream budget/call/audit caps.',
    'A': 'Reuse A/W reports/arrays/code/full pool; no routine rechecks/refits. Check decision-changing conflicts/gaps; expand as needed. New candidates/tuning/bulk retests: W. Changed bases limit comparisons. End: 2-3 full procedures if useful/affordable; no quota/score default. Gaps/bottlenecks/assumption/criteria changes: revise_h(minimal executable test); else continue_w. May reject all. Publish own reports/executed code+helpers to outputs; cite managed originals.',
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
