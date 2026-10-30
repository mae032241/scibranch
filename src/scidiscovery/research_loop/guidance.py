"""Short scientific role advice, frozen as literal text when preparing a task."""
MAX_BYTES = 2560
WORK_UNITS = {
    'screen': 'Compare assigned mechanisms cheaply; report observations or obstacles. Calls alone do not establish screening.',
    'improve': 'In H, follow findings/global unmet goals beyond a recipe/contrast. Return to A for required independent judgment, strategic choices/tradeoffs, direction-changing counterevidence, stall/scope change/blocker or actual limits. Retain spent budgets.',
    'validate': 'Resolve the assigned decision-changing gap with fair controls; stop when resolved or blocked, without a new search or unlimited proof.',
    'repair': 'Repair the exact failed observation within H and the original allowance. Preserve checks/accepted evidence; reconcile unknown calls, never replay.',
}

DEFAULT = {
    'H': 'Choose screen for a cheap mechanism comparison; improve/repair/validate name one mechanism. Compare plausible explanations before concentrating; justify a single line. Each minimal_test states differing predictions, the discriminating signal, priority and reopening condition. Where useful, compare 2-3 mechanisms with the same data/evaluation in one W; this is no quota. Preserve user/task constraints, required counts and commitments; request E as needed.',
    'W': 'Own methods/candidates within H; A suggests methods. Honor checks; use --mechanism. Initial/grant calls protect screening opportunities, not trial totals. Route scope changes via A to H; reconcile unknown calls, never replay. Return to independent A, not global completion. No auxiliary agents.',
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
    'H': 'Choose mechanisms that differ in key assumptions or failure modes, not labels or parameter variants. Revisit live alternatives after screening when findings or stalls warrant. H owns questions, mechanisms and criteria; W owns methods and grids. Do not allocate downstream token/time budgets or execution/call/audit caps; control allowances govern.',
    'A': 'Read W and prior A deltas, discover only missing names and retain useful scripts in artifacts/. Check goal alignment of proxies, validation and audits, conditional rank reversals and same-family tuning. Request bounded public work: continue_w for W methods/improvement and reassessment; revise_h for H mechanisms, criteria or assumptions.',
    'W': 'Implement/validate incrementally within scientific scope/control allowance; use evidence/current budget. H/A add no caps; honor work-unit returns.',
}



def policy(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Guidance requires exact H/W/A/E scientific text')
    if any(not isinstance(text, str) or not text.strip() for text in value.values()):
        raise ValueError('Role guidance must contain scientific text')
    if sum(len(text.encode()) for text in value.values()) > MAX_BYTES:
        raise ValueError('All scientific role guidance exceeds its frozen byte bound')
    return {role: value[role] for role in DEFAULT}
