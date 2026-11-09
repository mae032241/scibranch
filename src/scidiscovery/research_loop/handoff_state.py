"""Controller-owned access to already sealed facts.

A seal closes verification. Loading it reads its saved facts; it does not renew
old input checks or execution permissions. New inputs still use normal resolvers.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
import stat

_active = ContextVar('research_handoff_state', default=None)
_loading = ContextVar('research_loading_sealed', default=False)


@dataclass
class State:
    root: Path
    task_step: int | None = None
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


def begin_job(root,step):
    """Keep disposable reads local to one task, never an accumulating prefix."""
    state=current(root)
    if state is None or state.task_step==step:return
    state.task_step=step
    for name in ('sources','handles','observations','results','records','search','recoveries'):
        getattr(state,name).clear()
    state.sequence=None
    state.sequence_before=1


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


def _accepted_marker(root, step, owner):
    """Require the published acceptance marker, without replaying its checks."""
    state=current(root)
    if state is not None and step in state.results and state.handles.get(step) == owner:
        return
    from . import originals
    path=originals.source_record(owner,'result-identity.json')
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('Scientific result has no regular acceptance marker')


def sealed_facts(root, step, *, base=None, owner=None, known=None):
    """Load one exact sealed producer; its authored body is loaded only on demand."""
    from . import contracts as c, originals
    if owner is None:
        owner = (originals.node(root, step) if base is None
            else originals.node_from_base(root, step, base=base))
    if owner.step != step:
        raise ValueError('Sealed source belongs to another logical node')
    seal_ref = dict(path=str(Path(owner.original_root) / f'native-evidence/{step:03d}/result-identity.json'))
    key = (owner.origin_identity['goal_id'], owner.origin_identity['settings_ref']['path'],
        owner.original_root, str(owner.storage_root), step, seal_ref['path'], owner.native_record)
    state = current(root)
    if known is None and state is not None and key in state.sources:
        return state.sources[key]
    with loading():
        _accepted_marker(root,step,owner)
        job = known['job'] if known is not None and 'job' in known else originals.source_job(owner)
        seal_path = originals.source_record(owner, 'result-identity.json')
        seal = known['seal'] if known is not None and 'seal' in known else c.read(seal_path)
        if seal['job_ref'] != dict(path=str(Path(owner.original_root) / f'jobs/{step:03d}.json')):
            raise ValueError('Source seal changed its exact original job')
        raw = Path(seal['raw_ref']['path'])
        origin = Path(owner.original_root) / f'native-evidence/{step:03d}'
        if not raw.is_relative_to(origin) or raw.name != 'science-output.json':
            raise ValueError('Source seal lost its exact native attempt')
        relative = raw.parent.relative_to(origin)
        if relative.parts and (len(relative.parts) != 2 or relative.parts[0] != 'continuations'
                or not relative.parts[1].isdecimal() or relative.parts[1] != f'{int(relative.parts[1]):03d}'):
            raise ValueError('Source seal lost its exact native attempt')
        if owner.native_record is not None and raw.parent != Path(owner.original_root) / owner.native_record:
            raise ValueError('Source seal differs from its retained native attempt')
        raw_path = originals.resolve_ref(owner, seal['raw_ref'])
        facts = {} if known is None else known
        facts.update(owner=owner, job=job, seal=seal, seal_ref=seal_ref,
            seal_path=seal_path, raw_path=raw_path)
    if state is not None:
        state.sources[key] = facts
    return facts


def load_material_sources(root, refs, *, sources, base=None, source=None):
    """Load each explicitly selected producer list once for the operation owner."""
    from . import originals, material_references
    for group in refs:
        ref = group['list_ref']
        step = ref['step']
        if type(step) is not int or step < 1:
            raise ValueError('Current material list lost its exact original producer')
        if step not in sources or 'seal' not in sources[step]:
            known = sources.get(step)
            if known is not None:
                owner = known['owner']
            elif source is not None:
                owner = originals.at_step(originals.source_for_ref(source, ref), step)
            else:
                owner = originals.for_origin(root, ref['origin_identity'], step=step)
            sources[step] = sealed_facts(root, step, base=base, owner=owner, known=known)
        if material_references.list_ref(sources[step]) != ref:
            raise ValueError('Current material list differs from its exact producer')
    return sources


def material_inputs(root, job, *, sources, base=None, source=None):
    """Produce this current job's rows; historical job reads stay metadata only."""
    from . import material_references
    refs = job['material_refs']
    load_material_sources(root, refs, sources=sources, base=base, source=source)
    loaded = dict(job, direct_materials=material_references.materials(refs, sources=sources))
    if 'selected_inputs' in job:
        loaded['selected_inputs'] = material_references.selected_materials(job['selected_inputs'], sources=sources)
    if job.get('formal_review'):
        from . import formal_review
        reference = job['formal_review']
        if source is not None:
            reference = dict(reference, origin_identity=reference.get('origin_identity', source.origin_identity))
        binding = formal_review.input_binding(root, reference, sources=sources, owner=source)
        scientific = binding.get('compiler_scientific_materials', [])
        expected = material_references.references(
            [row for row in binding['materials'] if row not in scientific], sources=sources)
        if refs != expected:
            raise ValueError('Formal review material references changed their exact subject')
        loaded['direct_materials'] = binding['materials']
    return loaded


def material_paths(root, job, *, sources):
    """Accept the owner's selected original paths using its loaded producer lists."""
    from . import originals
    result = {}
    for material in job.get('direct_materials', []):
        if job.get('formal_review') and material['path'] == 'inputs/review/scientific-implementation.json':
            result[material['path']] = originals.resolve_root_ref(root,
                dict(material, path=material['source']))
            continue
        step = int(Path(material['path']).parts[2])
        producer = sources[step]
        result[material['path']] = originals.bound_material_path(root, material,
            source=producer['owner'], seal=producer['seal'])
    return result


def selected_materials(root, seal, *, sources, base=None, source=None):
    """Resolve a saved selection from its explicit producer lists only."""
    from . import material_references
    rows = seal.get('selected_inputs', [])
    refs = material_references.selected_refs(rows)
    load_material_sources(root, refs, sources=sources, base=base, source=source)
    return material_references.selected_materials(rows, sources=sources)


def relayed_materials(root, seal, *, sources, base=None, source=None):
    """Resolve the exact current relay without following ancestor relays."""
    from . import material_references
    refs = seal['relayed_material_refs']
    load_material_sources(root, refs, sources=sources, base=base, source=source)
    return material_references.materials(refs, sources=sources)


def _observation_facts(facts):
    if 'observation' not in facts:
        from . import contracts as c, originals
        receipt = facts['seal'].get('observations')
        with loading():
            facts['observation'] = (c.read(originals.resolve_ref(facts['owner'], receipt))
                if receipt is not None else None)
    return facts['observation']


def source_facts(root, step, *, base=None, owner=None, resolve_choices=True):
    """Produce original and control views from one parsed author response."""
    return source_facts_from_sealed(root,sealed_facts(root,step,base=base,owner=owner),
        resolve_choices=resolve_choices)


def source_facts_from_sealed(root, facts, *, resolve_choices=True):
    """Complete the owner's existing sealed facts without reopening metadata."""
    from . import contracts as c, candidate_refs
    if 'payload' not in facts:
        original = c.read_scientific_source(facts['raw_path'])
        facts.update(payload=original['payload'], raw_text=original['raw_text'])
    if resolve_choices and 'control' not in facts:
        job, payload, owner = facts['job'], facts['payload'], facts['owner']
        nominations = payload.get('candidate_nominations')
        dict_choices = isinstance(nominations, dict) and any(
            isinstance(row, dict) and isinstance(row.get('candidate'), dict)
            for field in ('representatives', 'extras')
            for row in (nominations.get(field, []) if isinstance(nominations.get(field, []), list) else []))
        if dict_choices or owner.inherited and job['role'] == 'W':
            _observation_facts(facts)
        with loading():
            control = candidate_refs.control_payload(job, payload, root=root,
                source=owner if owner.inherited else None, source_facts=facts)
            projected_job = job
            if owner.inherited:
                if 'input_mapping' not in facts:
                    facts['input_mapping'] = candidate_refs.source_input_mapping(owner, job)
                mapping = facts['input_mapping']
                projected_job = candidate_refs.source_job_view(owner, job, input_mapping=mapping)
                science_mapping = dict(mapping)
                if job['role'] == 'W':
                    science_mapping.update({row['id']: candidate_refs.origin_candidate(owner.origin_identity, row['id'])
                        for row in (facts.get('observation') or {}).get('candidates', [])})
                control = candidate_refs.public_science_fields(control, science_mapping)
            facts['control'] = (projected_job, control, facts['seal'], facts['seal_ref'])
    return facts


def source_from_facts(facts, *, resolve_choices=True):
    """Select a produced view without loading or requalifying its source."""
    if resolve_choices:
        return facts['control']
    return facts['job'], facts['payload'], facts['seal'], facts['seal_ref']


def source(root, step, *, resolve_choices=True):
    return source_from_facts(source_facts(root, step, resolve_choices=resolve_choices),
        resolve_choices=resolve_choices)


def observation(root, job, seal):
    receipt = seal.get('observations')
    if receipt is None:
        return None
    step = job['step']
    facts = sealed_facts(root, step)
    if facts['seal'] != seal:
        raise ValueError('Observation belongs to another sealed source')
    return observation_from_facts(root, facts)


def observation_from_facts(root, facts):
    """Load the saved observation using this owner's already accepted source."""
    if facts['seal'].get('observations') is None:return None
    from . import candidate_refs
    state = current(root)
    step = facts['job']['step']
    if 'observation_view' in facts:
        return facts['observation_view']
    with loading():
        owner = facts['owner']
        value = _observation_facts(facts)
        if owner.inherited:
            if 'input_mapping' not in facts:
                facts['input_mapping'] = candidate_refs.source_input_mapping(owner, facts['job'])
            value = candidate_refs.source_observation(owner, facts['job'], value,
                input_mapping=facts['input_mapping'])
    facts['observation_view'] = value
    if state is not None:
        state.observations[step] = value
    return value


def read_sealed(root, step):
    from . import contracts as c, originals
    state = current(root)
    if state is not None and step in state.results:
        return state.results[step]
    with loading():
        owner=originals.node(root,step)
        _accepted_marker(root,step,owner)
        value = c.read(originals.source_record(owner, 'result.json'))
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
