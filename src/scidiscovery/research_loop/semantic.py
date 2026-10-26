"""Replayed scientific-control outcomes and finite author clarification."""
from __future__ import annotations

from pathlib import Path

from . import execution

from . import contracts as c, history, search

MAX_DIAGNOSTICS = 8
MAX_AUTHOR_CORRECTIONS = 2


def outcome(root,job,payload):
    from .protocol import CURRENT
    if type(job.get('handoff_protocol')) is not int or job['handoff_protocol']!=CURRENT:
        raise ValueError('Current scientific interpretation required')
    from . import formal_review
    formal_review.validate_choice(root,job,payload)
    enabled=c.settings(root, job).get('search_policy') is not None and not job.get('formal_review') and not job.get('final_report')
    interpretation=search.interpret(root,job,payload) if enabled else dict(focus=None,allocation=None,diagnostics=[])
    search.require_portfolio_review(root,job,payload,interpretation=interpretation)
    from . import candidate_nominations
    candidate_nominations.require_final_coverage(root,job,payload)
    diagnostics=interpretation['diagnostics']
    selection=[];c.selected_inputs(c.workspace(root,job['step']),payload,job,selection)
    from . import decision
    requests=decision.assess(root,job,payload,focus=interpretation['focus'])
    delivery=None
    if job['role']=='W':
        from . import worker_delivery
        delivery=worker_delivery.require_delivery(root,job,payload,execution.record(root,job['step']))
    rows=[*diagnostics,*selection,*requests,*history.validate_recommendation(job,payload,root=root)]
    rows.extend(row for row in c.scientific_diagnostics(job,payload) if row not in rows)
    return dict(**(dict(stage_end_reason='continuation_unavailable') if job['role']=='W' and payload.get('stage_intent')=='continue' and not job.get('worker_fragment_policy') else {}),
        **(dict(worker_delivery=delivery) if delivery is not None else {}),
        binding_protocol=CURRENT,resolved_focus=interpretation['focus'],resolved_allocation=interpretation['allocation'],
        search_status='unresolved' if diagnostics else 'applied',selection_status='unresolved' if selection else 'applied',
        decision_status='unresolved' if requests else 'applied',
        diagnostics=rows[:MAX_DIAGNOSTICS],omitted_count=max(0,len(rows)-MAX_DIAGNOSTICS))


def _source(root, step):
    job, payload, seal, seal_sha = history._source(root, step)
    return dict(step=step, job_ref=seal['job_ref'], raw_ref=seal['raw_ref'], seal_ref=seal_sha)


def public_request(root, job):
    """Bound scientific clarification, without the private identity chain."""
    request = job['author_correction']
    from . import fork_runtime
    first = c.read(fork_runtime.source_record(root, request['root']['step'], 'result-identity.json'))
    diagnostics = first['control_outcome']['diagnostics']
    from . import role_share
    if request['reason'] in role_share.REFUSALS:
        diagnostics = [dict(code=request['reason'], recovery='Choose another scientific route under the current admission limits. No new evidence or budget was supplied.')]
    if request['reason']=='formal_review_unavailable':
        from . import formal_review
        diagnostics=[dict(code=formal_review.unavailable(root,request['root']['step'])['failure']['reason'],
            recovery='Choose another scientific action, a different exact subject, or supply missing verifiable identity evidence. No qualification was granted.')]
    if job.get('purpose') == 'closeout':
        from . import delivery
        original, payload, _, _ = history._source(root, request['root']['step'])
        try:
            delivery.binding(root, original, payload)
        except delivery.DeliveryUnresolved as error:
            diagnostics = [dict(code=error.code, subject=payload.get('candidate_recommendation'))]
    source_step = request['source']['step']
    return dict(reason=request['reason'], source_step=source_step,
                original_science=f'inputs/parents/{source_step:03d}/conclusion.json',
                initial_attempt_step=request['root']['step'],
                unresolved=diagnostics,
                remaining_author_corrections=max(0, MAX_AUTHOR_CORRECTIONS - accepted(root, request, job['step'])))


def validate_job(root, job):
    request = job.get('author_correction')
    if request is None:
        return
    if (job['role'] not in {'H', 'A', 'E'}
            or not isinstance(request, dict) or set(request) != {'root', 'source', 'reason'}
            or not isinstance(request['reason'], str)):
        raise ValueError('Author correction binding is invalid')
    for field in ('root', 'source'):
        ref = request[field]
        if (not isinstance(ref, dict) or type(ref.get('step')) is not int or not 0 < ref['step'] < job['step']
                or ref != _source(root, ref['step'])):
            raise ValueError('Author correction original identity changed')
    from . import fork_runtime
    source = fork_runtime.source_job(root, request['source']['step'])
    original = source.get('author_correction')
    if request['reason']=='formal_review_unavailable' and job.get('purpose')!='closeout':
        from . import formal_review
        attempts=formal_review.unavailable_attempts(root,request['source']['step'])
        if (request['root']['step']!=attempts[0]
                or not any(ref['path']==f'inputs/parents/{request["source"]["step"]:03d}/conclusion.json'
                           for ref in job['direct_refs'])):
            raise ValueError('Unavailable review correction must retain its first exact failure and actual author')
        return
    if original is not None and original['reason']!='formal_review_unavailable' and (request['root'] != original['root'] or request['reason'] != original['reason']):
        raise ValueError('Author correction root cannot change across retries')
    if (original is None or original['reason']=='formal_review_unavailable') and request['root'] != request['source']:
        raise ValueError('Author correction root is not its first attempt')
    root_step = request['root']['step']
    root_job, root_payload, root_seal, _ = history._source(root, root_step)
    from . import role_share
    if request['reason'] in role_share.REFUSALS:
        action = root_payload.get('next_action')
        optional = (root_job['role'], action) in {('H', 'analyze'), ('A', 'revise_h'), ('A', 'audit'), ('A', 'formal_review')}
        optional = optional or root_job['role'] == 'E' and (root_job.get('evidence_return') or {}).get('role') in {'H', 'A'}
        optional = optional or root_job.get('formal_review') is not None
        if root_job['role'] == 'A' and action == 'wait':
            marker = root / 'host-control' / 'wait-continuations' / f'{root_step:03d}.json'
            expected = dict(source_step=root_step, seal_ref=request['root']['seal_ref'])
            if not marker.exists() or c.read(c.regular(marker)) != expected:
                raise ValueError('Admission correction requires its exact explicit wait continuation')
            optional = True
        if (not optional or job.get('purpose') == 'closeout'
                or not any(ref['path'] == f'inputs/parents/{request["source"]["step"]:03d}/conclusion.json' for ref in job['direct_refs'])):
            raise ValueError('Admission correction must bind its exact refused optional decision')
        # The immutable host job records the actual refusal made at its creation.
        # Rechecking against an amended total would rewrite that historical fact.
        return
    if job.get('purpose') == 'closeout':
        from . import budget, delivery
        begin = budget.closeout_state(root)['begin']
        if root_job['role'] != 'A' or not (root_job.get('purpose') == 'closeout'
                or begin.get('authorization_step') == root_step):
            raise ValueError('Closeout author correction has no exact prior delivery decision')
        try:
            delivery.binding(root, root_job, root_payload)
        except delivery.DeliveryUnresolved as error:
            if error.code != request['reason']:
                raise ValueError('Closeout author root reason changed')
        else:
            raise ValueError('A resolved delivery decision cannot be reinterpreted as a correction')
        if not any(ref['path'] == f'inputs/parents/{request["source"]["step"]:03d}/conclusion.json' for ref in job['direct_refs']):
            raise ValueError('Closeout correction must bind its exact prior author')
        return
    from . import decision
    decision_reason = decision.reason(root, root_job, root_payload)
    decision_actions = decision.ACTIONS if decision_reason else {'continue_w': 'W'}
    if (root_job['role'] == 'A' and root_payload.get('next_action') in decision_actions and decision_reason):
        if request['reason'] != decision_reason:
            raise ValueError('Author decision correction differs from its original diagnostic')
        if not any(ref['path'] == f'inputs/parents/{request["source"]["step"]:03d}/conclusion.json' for ref in job['direct_refs']):
            raise ValueError('Decision correction must bind its actual author')
        return
    if (root_job['role'], root_payload.get('next_action')) not in {('H', 'experiment'), ('A', 'continue_w')}:
        raise ValueError('Author correction root did not request a dependent experiment')
    reason = 'implementation_unresolved' if root_seal.get('control_outcome', {}).get('selection_status') == 'unresolved' else None
    if reason is None and c.settings(root, job).get('search_policy') is not None:
        protocol = root_job.get('handoff_protocol', 1)
        reason = search.launch_reason(root, dict(step=root_step + 1, role='W', handoff_protocol=protocol,
            search=search.bind(root, root_step + 1, 'W', protocol, write_pages=False, unified=True)))
    if request['reason'] != reason:
        raise ValueError('Author correction reason differs from its original blocked action')
    if not any(ref['path'] == f'inputs/parents/{request["source"]["step"]:03d}/conclusion.json' for ref in job['direct_refs']):
        raise ValueError('Author correction must bind its actual source attempt')


def accepted(root, request, before_step):
    if request['reason']=='formal_review_unavailable':
        from . import formal_review
        return max(0,len(formal_review.unavailable_attempts(root,request['root']['step'],before_step=before_step))-1)
    from . import fork_runtime, originals
    original = fork_runtime.source_job(root, request['root']['step'])
    from . import budget
    begin = budget.closeout_state(root)['begin']
    if original.get('purpose') == 'closeout' or begin is not None and begin.get('authorization_step') == original['step']:
        return max(0, closeout_errors(root, before_step) - 1)
    count = 0
    sources = {source.step: source for source in originals.source_sequence(root, before_step)}
    sources.update((step, originals.node(root, step)) for step in fork_runtime.local_steps(root) if step < before_step)
    for step, source in sorted(sources.items()):
        if step <= request['root']['step']:
            continue
        job = originals.source_job(source)
        previous = job.get('author_correction')
        if previous is not None and previous['root'] == request['root'] and job['role'] in {'H', 'A'}:
            if not source.inherited and not (execution.record(root, step) / 'native-intent.json').exists():
                continue
            intent = originals.source_record(source, 'native-intent.json')
            path = originals.resolve_ref(source, dict(path=f'jobs/{step:03d}.json'))
            if c.read(c.regular(intent)).get('task_ref') != dict(path=str(Path(source.original_root)/f'jobs/{step:03d}.json')):
                raise ValueError('Author correction execution identity changed')
            count += 1
    return count


def closeout_errors(root, before_step):
    """Invalid closeout choices only; audit and deterministic failure are normal turns."""
    from . import budget, delivery
    begin = budget.closeout_state(root)['begin']
    errors = 0
    for path in sorted((root / 'jobs').glob('*.json')):
        job = c.read(path)
        if job['step'] >= before_step:
            break
        if job['role']!='A':continue
        if (job.get('purpose') != 'closeout' and (begin or {}).get('authorization_step') != job['step']) or not (c.host_record(c.workspace(root, job['step'])) / 'result-identity.json').exists():
            continue
        science = c.read(execution.raw(root, job['step']))
        if science['next_action'] in {'audit', 'finish', 'wait'}:
            continue
        try:
            delivery.binding(root, job, science)
        except delivery.DeliveryUnresolved as error:
            errors += error.code not in {'delivery_source_unprepared', 'selection_comparison_required'}
    return errors



def effective_route(root, step, result, job, route):
    """Refuse dependent W admission before creating its immutable task."""
    role, parents, instruction, evidence_return, current_h, current_w, correction_request = route
    request = None
    previous = job.get('author_correction')
    # Historical review-only corrections do not constrain ordinary science.
    # Exact unavailable originals now constrain the request before sealing.
    if previous is not None and previous['reason']=='formal_review_unavailable':
        previous=None
    seal = c.read(c.host_record(c.workspace(root, step)) / 'result-identity.json')
    from . import decision
    decision_code = decision.reason(root, job, result)
    if (evidence_return or {}).get('kind')=='formal_review_unavailable':
        if previous is not None:
            request=dict(previous,source=_source(root,step))
    elif decision_code is not None:
        source = _source(root, step)
        request = dict(root=previous['root'] if previous else source, source=source,
                       reason=previous['reason'] if previous else decision_code)
        role, parents = 'A', list(dict.fromkeys([*parents, step]))
        instruction = ('Clarify the scientific decision, concrete question and result that would change it. '
                       'The original science is retained; no dependent research is authorized: ' + decision_code + '.')
        evidence_return, correction_request = None, None
    elif role == 'W':
        from . import engineering
        repair = engineering.request(root, max(parents)) if parents else None
        if repair is not None:
            engineering.admit(root, repair, step + 1)
        code = 'implementation_unresolved' if repair is None and seal.get('control_outcome', {}).get('selection_status') == 'unresolved' else None
        if code is None and c.settings(root, job).get('search_policy') is not None:
            protocol = job.get('handoff_protocol', 1)
            planned = dict(step=step + 1, role='W', handoff_protocol=protocol,
                           engineering_repair=repair,
                           search=None if repair is not None else search.bind(root, step + 1, 'W', protocol, write_pages=False, unified=True))
            code = search.launch_reason(root, planned)
        if code is not None:
            source = _source(root, step)
            request = dict(root=previous['root'] if previous else source, source=source,
                           reason=previous['reason'] if previous else code)
            role = job['role'] if job['role'] in {'H', 'A'} else 'H'
            if role != 'H':
                correction_request = None
            elif job['role'] == 'H':
                correction_request = job.get('correction_request')
            parents = list(dict.fromkeys([*parents, step]))
            instruction = ('Correct this unresolved scientific control choice: ' + code + '. '
                'The original science is retained; no dependent experiment is authorized. '
                'Declare only the needed scientific change, or choose a justified alternative route.')
            evidence_return = None
    elif previous is not None and role in {'H', 'A', 'E'}:
        # Extraction may gather science, but it does not renew author retries.
        request = dict(previous, source=_source(root, step))
        if step not in parents:
            parents = [*parents, step]
    return role, parents, instruction, evidence_return, current_h, current_w, correction_request, request
