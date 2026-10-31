"""Controller-owned access to already sealed facts.

A seal closes verification. Loading it reads its saved facts; it does not renew
old input checks or execution permissions. New inputs still use normal resolvers.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

_active = ContextVar('research_handoff_state', default=None)
_loading = ContextVar('research_loading_sealed', default=False)


@dataclass
class State:
    root: Path
    sources: dict = field(default_factory=dict)
    handles: dict = field(default_factory=dict)
    fork_manifest: dict | None = None
    fork_snapshot: object | None = None
    fork_prefix: dict = field(default_factory=dict)
    sequence: list | None = None
    sequence_before: int = 1
    observations: dict = field(default_factory=dict)
    results: dict = field(default_factory=dict)
    records: dict = field(default_factory=dict)
    search: dict = field(default_factory=dict)
    recoveries: dict = field(default_factory=dict)


def current(root):
    state = _active.get()
    return state if state is not None and state.root == Path(root).absolute() else None


@contextmanager
def scope(root):
    root = Path(root).absolute()
    state = current(root)
    if state is not None:
        yield state
        return
    token = _active.set(State(root))
    try:
        yield _active.get()
    finally:
        _active.reset(token)


@contextmanager
def loading():
    token = _loading.set(True)
    try:
        yield
    finally:
        _loading.reset(token)


def loading_sealed():
    return _loading.get()


def source(root, step, *, resolve_choices=True):
    from . import contracts as c, originals, candidate_refs
    state = current(root)
    key = (step, resolve_choices)
    if state is not None and key in state.sources:
        return state.sources[key]
    with loading():
        owner = originals.node(root, step)
        job = originals.source_job(owner)
        seal_path = originals.source_record(owner, 'result-identity.json')
        seal = c.read(seal_path)
        payload = c.read_scientific_output(originals.resolve_ref(owner, seal['raw_ref']))
        if resolve_choices:
            payload = candidate_refs.control_payload(job, payload, root=root, source=owner if owner.inherited else None)
            if owner.inherited:
                mapping = candidate_refs.source_input_mapping(owner, job)
                if job['role'] == 'W' and seal.get('observations'):
                    observed = c.read(originals.resolve_ref(owner, seal['observations']))
                    mapping.update({row['id']: candidate_refs.origin_candidate(owner.origin_identity, row['id']) for row in observed['candidates']})
                payload = candidate_refs.public_science_fields(payload, mapping)
        if owner.inherited:
            job = candidate_refs.source_job_view(owner, job)
        seal_ref = dict(path=str(Path(owner.original_root) / f'native-evidence/{step:03d}/result-identity.json'))
        row = (job, payload, seal, seal_ref)
    if state is not None:
        state.sources[key] = row
    return row


def observation(root, job, seal):
    receipt = seal.get('observations')
    if receipt is None:
        return None
    from . import contracts as c, originals, candidate_refs
    state = current(root)
    step = job['step']
    if state is not None and step in state.observations:
        return state.observations[step]
    with loading():
        owner = originals.node(root, step)
        value = c.read(originals.resolve_ref(owner, receipt))
        if owner.inherited:
            value = candidate_refs.source_observation(owner, originals.source_job(owner), value)
    if state is not None:
        state.observations[step] = value
    return value


def read_sealed(root, step):
    from . import contracts as c, originals
    state = current(root)
    if state is not None and step in state.results:
        return state.results[step]
    with loading():
        value = c.read(originals.source_record(originals.node(root, step), 'result.json'))
    if state is not None:
        state.results[step] = value
    return value


def encode_search(state):
    """JSON form of reducer counters; scientific originals are never embedded."""
    import copy
    value = copy.deepcopy(state)
    value['authorized'] = sorted(value['authorized'])
    value['order']['seen_w'] = list(value['order']['seen_w'])
    value['portfolio']['seen_w'] = [list(item) for item in value['portfolio']['seen_w']]
    value['opportunity_grants'] = [[list(key), count] for key, count in value['opportunity_grants'].items()]
    return value


def decode_search(value):
    import copy
    state = copy.deepcopy(value)
    state['authorized'] = set(state['authorized'])
    state['order']['seen_w'] = {tuple(item) if isinstance(item, list) else item for item in state['order']['seen_w']}
    state['portfolio']['seen_w'] = {tuple(item) for item in state['portfolio']['seen_w']}
    state['opportunity_grants'] = {tuple(key): count for key, count in state['opportunity_grants']}
    pending = state['portfolio']['pending_w']
    if pending is not None:
        pending['identity'] = tuple(pending['identity'])
    for row in state['portfolio'].get('cycles', []):
        row['w_identity'] = tuple(row['w_identity'])
    return state


def remember_search(state, key, value):
    """Keep adjacent forward cuts, not a copy of every growing prefix."""
    state.search[key]=value
    while len(state.search)>2:
        state.search.pop(min(state.search,key=lambda cut:len(cut[0])))


def publish_search(root):
    """Checkpoint only the reducer summary, after an owned seal publication."""
    from . import contracts as c
    state=current(root)
    if state is None or not state.search:
        return
    # Equal source cuts can have later accepted recovery facts. Preserve the
    # most recently derived state at that cut, rather than its first version.
    (steps,refs),value=max(reversed(list(state.search.items())),key=lambda item:len(item[0][0]))
    snapshot=Path(root)/'host-history/search-state.json'
    if snapshot.exists():
        saved=c.read(snapshot)
        if len(saved['steps'])>len(steps):return
    import json
    c.write(snapshot,dict(steps=list(steps),recovery_refs=json.loads(refs),state=encode_search(value)))
