"""Exact implementation repair after a known, cleaned audit engineering fault.

Repairs never rewrite a sealed owner. One new W per original candidate lineage
may produce a new candidate; A must judge and authorize those new bytes.
"""
from __future__ import annotations

from . import configuration_updates
from . import contracts as c, execution, history, originals


class RepairUnavailable(ValueError):
    pass


def _failure(root, decision_step):
    from . import budget, candidate_audit
    receipt = candidate_audit._outcome(root, decision_step)
    if receipt is not None:
        path = candidate_audit._directory(root, decision_step)/'outcome.json'
        binding_path = path.with_name('binding.json')
        binding = c.read(c.regular(binding_path)) if binding_path.exists() else None
        result=receipt['result']
        sources=path.with_name('sources.json')
        if result['status']=='unavailable' and sources.exists():
            rows=c.read(c.regular(sources))['items']
            if any(row['candidate_id']==receipt['candidate_id'] and row.get('reason')=='delivery_code_source_unavailable' for row in rows):
                result=dict(status='engineering_error',note='implementation_source_missing',cleanup_confirmed=True,recovery='author_repair')
                binding=dict(candidate_id=receipt['candidate_id'],source=dict(objects={}))
        return result, path, binding
    actions = [row for row in budget.closeout_state(root)['actions']
               if row['intent']['decision']['step'] == decision_step and row['outcome'] is not None]
    if not actions:
        for path in reversed(sorted((root/'host-delivery/preparation').glob('*.json'))):
            value=c.read(c.regular(path))
            if value.get('decision_step')==decision_step and value['reason']=='implementation_source_missing':
                return dict(status='engineering_error',note=value['reason'],cleanup_confirmed=True,recovery='author_repair'),path,dict(candidate_id=value['candidate_id'],source=dict(objects={}))
        return None
    row = actions[-1]
    path = root/'control-ledger'/f'{row["outcome"]["seq"]:06d}.json'
    return row['outcome']['result'], path, row['intent']['binding']


def request(root, decision_step):
    if not (root / 'jobs' / f'{decision_step:03d}.json').exists():
        from . import fork_runtime, fork_sources, originals
        if not fork_runtime.is_fork(root):
            return None
        selected = fork_sources.selected_task(root)
        repair = selected['job'].get('engineering_repair')
        if repair is None or repair['decision_step'] != decision_step:
            return None
        context = originals.source_for_ref(selected['source'], repair['failure'])
        c.read(originals.resolve_ref(context, repair['failure']))
        return repair
    failure = _failure(root, decision_step)
    if failure is None:return None
    return _request(root,decision_step,failure)


def _request(root,decision_step,failure):
    result, path, binding = failure
    if (result['status'] != 'engineering_error' or not result['cleanup_confirmed']
            or result.get('recovery')!='author_repair' or binding is None):return None
    from . import accepted_prefix, originals
    job = originals.source_job(originals.node(root, decision_step))
    cid = binding['candidate_id']
    prefix = accepted_prefix.for_job(job)
    try:
        candidate = accepted_prefix.candidate(root, prefix, cid)
    except FileNotFoundError:
        return None
    except ValueError as error:
        if str(error) == 'Candidate reference is outside this frozen scientific task':return None
        raise
    member = accepted_prefix.member(root, prefix, candidate['first_step'],
        origin=candidate['origin_identity'])
    source = dict(step=member['step'], seal_ref=member['seal_ref'])
    owner = originals.source_job(originals.node(root, source['step']))
    if owner['role']!='W':return None
    original = (owner.get('engineering_repair') or {}).get('original_candidate',cid)
    return dict(decision_step=decision_step, candidate_id=cid, original_candidate=original,
        source=source, reason=result['note'], failure=dict(path=path.relative_to(root).as_posix()),
        objects=binding['source']['objects'],diagnostic=result.get('diagnostic',result['note']))


def _blocked_path(root,repair):
    from scidiscovery import execution_security as security
    from . import delivery
    key=security.request_digest(dict(settings_ref=c.record_ref(root/'settings.json'),repair=repair))
    return root/'host-delivery/engineering-blocked'/(key+'.json')


def _lineage_key(root, repair):
    from . import originals
    source = originals.node(root, repair['source']['step'])
    active = set()
    while True:
        key = (originals.identity_key(source.origin_identity), source.step)
        if key in active:
            raise ValueError('Engineering repair lineage contains a source cycle')
        active.add(key)
        owner = originals.source_job(source)
        previous = owner.get('engineering_repair')
        if previous is None:
            return (*key, repair['original_candidate'])
        source = originals.dependency(source, previous['source']['step'],
            seal_ref=previous['source']['seal_ref'])


def admit(root, repair, before_step):
    expected=request(root,repair['decision_step'])
    if repair != expected:
        raise ValueError('Engineering repair differs from the exact audit diagnosis')
    blocked=_blocked_path(root,repair)
    if blocked.exists():
        value=c.read(c.regular(blocked,limit=4096))
        if value!={'repair_ref':blocked.stem,'reason':'engineering_repair_input_capacity'}:
            raise ValueError('Engineering repair preparation receipt changed')
        raise RepairUnavailable(value['reason'])
    from . import originals, fork_runtime
    sources = {source.step: source for source in originals.source_sequence(root, before_step)}
    sources.update((step, originals.node(root, step)) for step in fork_runtime.local_steps(root) if step < before_step)
    for source in sources.values():
        job = originals.source_job(source)
        prior=job.get('engineering_repair')
        if job['step']<before_step and prior and _lineage_key(root, prior) == _lineage_key(root, repair):
            raise RepairUnavailable('engineering_repair_exhausted')


def validate_job(root, job):
    repair=job.get('engineering_repair')
    if repair is None:return
    if (job['role']!='W' or repair['decision_step']>=job['step']
            or repair['source']['step']>=job['step']):raise ValueError('Invalid exact repair owner')
    admit(root,repair,job['step'])
    if job.get('search') is not None:raise ValueError('Engineering task cannot acquire a new scientific search scope')
    required={repair['decision_step'],repair['source']['step']}
    if not required <= {int(row['path'].split('/')[2]) for row in job['direct_refs']}:
        raise ValueError('Repair task omitted its scientific owner or failure author')
    from . import delivery
    for name in ('source.py','artifact'):
        proof=repair['objects'].get(name)
        if proof is None:
            if repair['reason']!='implementation_source_missing':raise ValueError('Repair task has no bound original')
            continue
        original=_repair_original(root, repair, proof)
        local=c.workspace(root,job['step'])/'inputs/repair'/name
        if c.regular(local).stat().st_size!=c.regular(original).stat().st_size:raise ValueError('Repair input copy changed')


def bind_inputs(root, job):
    from . import delivery
    from . import storage_policy as storage
    policy=configuration_updates.for_job(root, job)
    context=storage.Deadline(policy['max_seconds'])
    inventory=storage.Inventory(policy.get('storage_policy'),context=context)
    repair=job['engineering_repair']
    proofs=[repair['objects'][name] for name in ('source.py','artifact') if name in repair['objects']]
    if c.output_quota_exceeded(policy,(proof['size_bytes'] for proof in [*proofs,*job['direct_materials']])):
        from .candidate_audit import _save
        path=_blocked_path(root,repair)
        if not path.exists():_save(path,dict(repair_ref=path.stem,reason='engineering_repair_input_capacity'))
        raise RepairUnavailable('engineering_repair_input_capacity')
    for name in ('source.py','artifact'):
        proof=job['engineering_repair']['objects'].get(name)
        if proof is None:continue
        inventory.entry();inventory.file(proof['size_bytes'])
        c._bind_original(_repair_original(root,job['engineering_repair'],proof), c.workspace(root,job['step'])/'inputs/repair'/name, root=root, policy=policy.get('storage_policy'), context=context, size_bytes=proof['size_bytes'], source_kind='mutable')


def _repair_original(root, repair, proof):
    from . import delivery, fork_runtime, fork_sources, originals
    if (root / proof['path']).exists():
        return delivery._proof(root, proof)
    if not fork_runtime.is_fork(root):
        return delivery._proof(root, proof)
    selected = fork_sources.selected_task(root)
    if repair != selected['job'].get('engineering_repair'):
        raise ValueError('Repair original is outside its exact selected source')
    source = originals.source_for_ref(selected['source'], proof)
    return originals.resolve_ref(source, proof)


def public(repair):
    return dict(candidate_id=repair['candidate_id'],original_worker_step=repair['source']['step'],reason=repair['reason'],diagnostic=repair['diagnostic'],
        source='inputs/repair/source.py' if 'source.py' in repair['objects'] else None,artifact='inputs/repair/artifact' if 'artifact' in repair['objects'] else None,
        scope='Repair only this implementation fault within its original scientific method and constraints. '
            'Run a new candidate with --parent-plan naming this candidate and --change-intent. '
            'Do not submit, copy an old model as a new result, change the scientific method, or rewrite old conclusions. '
            'Audit uses a single script and declared frozen inputs; local sibling imports and runtime changes are not automatically captured. '
            'A decides whether the new candidate warrants delivery. If the fault requires a scientific change or a different runtime, report it to A.')


def steps(root, before_step=None):
    limit=float('inf') if before_step is None else before_step
    from . import originals, fork_runtime
    if fork_runtime.is_fork(root):
        boundary = int(limit) if before_step is not None else max(fork_runtime.local_steps(root), default=0) + 1
        return [source.step for source in originals.source_sequence(root, boundary)
            if originals.source_job(source).get('engineering_repair')]
    return [int(path.stem) for path in sorted((root/'jobs').glob('*.json'))
        if int(path.stem)<limit and c.read(c.regular(path)).get('engineering_repair')
        and (execution.origin(root,int(path.stem))/'result-identity.json').exists()]


def sources(root, before_step):
    from . import accepted_budget,budget,delivery
    head=accepted_budget.read(root)
    begin=budget.closeout_state(root,events=budget._events_from_state(head)[0])['begin']
    repaired=[step for step in steps(root,before_step) if step>begin['source_cutoff']]
    if not repaired:return begin['delivery_sources']
    nodes=[*begin['delivery_sources']['source_nodes'],*[dict(step=step,
        seal_ref=history.source_seal_ref(originals.node(root,step))) for step in repaired]]
    path=root/'jobs'/f'{before_step:03d}.json'
    job=c.read(c.regular(path)) if path.exists() else configuration_updates._reservation(root,before_step,head=head)
    return delivery.catalogue_sources(root,nodes,job=job)
