"""Role token shares of the effective authorization from reported usage; routing only, no new balance.

Optional H/A thinking is refused once its reported exploration usage reaches
the configured share of the current total authorization, or when
the same role repeats without new sealed W/E evidence or an audit receipt.
Required reviews stay legal: A after W, A after an audit receipt, control
correction review and closeout.
"""
from __future__ import annotations

from . import budget, contracts as c, execution

DEFAULT = dict(version=1, min_w_share=.65, max_thinking_share=.35, thinking_node_target_tokens=250_000)
THINKING = ('H', 'A')
REFUSALS = frozenset({'role_repeat_without_evidence', 'role_share_thinking_reached', 'role_share_w_floor_reached'})


def policy(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT) or type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Role budget policy requires version 1 shares and a thinking node target')
    shares = value['min_w_share'], value['max_thinking_share']
    if any(type(share) not in (int, float) or not 0 < share < 1 for share in shares) or sum(shares) > 1:
        raise ValueError('Role budget shares must be in (0, 1) and fit one authorization')
    if type(value['thinking_node_target_tokens']) is not int or value['thinking_node_target_tokens'] <= 0:
        raise ValueError('Thinking node target must be a positive integer')
    return dict(value)


def configured(root, job=None):
    value = c.settings(root, job).get('role_budget_policy')
    return policy(value) if value is not None else None


def usage(root, through_step):
    """Sum published attempt usage through this exact routing boundary."""
    from . import accepted_budget
    return accepted_budget.role_usage(root, through_step)


def _required(root, step, job, result, role, correction_request):
    if correction_request is not None or job['role'] == 'W' and role == 'A':
        return True
    if job['role'] == 'A' and role == 'A' and result.get('next_action') == 'audit' and c.settings(root, job).get('candidate_audit_policy'):
        from . import candidate_audit
        return candidate_audit.receipt_ref(root, step) is not None
    return False


def reason(root, step, job, result, role, correction_request=None, *, allow_repeat=False):
    """Why control refuses the proposed optional H/A node, or None."""
    limits = configured(root, job)
    if limits is None or role not in THINKING or job.get('purpose') == 'closeout':
        return None
    if _required(root, step, job, result, role, correction_request):
        return None
    if not allow_repeat and job['role'] == role and not (job.get('formal_review') or result.get('next_action')=='formal_review'):
        return 'role_repeat_without_evidence'
    meter, total = usage(root, step), budget.ceilings(root)['max_tokens']
    if meter['thinking'] >= limits['max_thinking_share'] * total:
        return 'role_share_thinking_reached'
    if meter['thinking'] + meter['E'] >= (1 - limits['min_w_share']) * total:
        return 'role_share_w_floor_reached'
    return None


def effective_route(root, step, result, job, route, *, allow_repeat=False):
    """Preserve required corrections; stop optional thinking without inventing work."""
    role, parents, instruction, evidence_return, current_h, current_w, correction_request, author = route
    # Author corrections are mandatory control repairs, not optional thinking.
    # Their existing finite correction allowance and global budget still apply.
    if author is not None and author['reason'] not in REFUSALS:
        return (*route, None)
    # A retained host correction owns the historical refusal. Later budget
    # amendments may change new admission, never that exact job's identity.
    from . import semantic
    path = root / 'jobs' / f'{step + 1:03d}.json'
    if path.exists():
        retained_job = c.read(c.regular(path))
        retained = retained_job.get('author_correction')
        if (retained is not None and retained['reason'] in REFUSALS
                and retained['source'] == semantic._source(root, step)):
            return (retained_job['role'], list(dict.fromkeys([*parents, step])), retained_job['instruction'],
                retained_job.get('evidence_return'), current_h, current_w,
                retained_job.get('correction_request'), retained, None)
        intent = execution.origin(root, step + 1) / 'native-intent.json'
        from . import accepted_budget
        accepted = accepted_budget.native_attempt(root, step+1, intent.parent)
        if accepted is not None:
            if accepted['intent'].get('task_ref') != c.record_ref(path):
                raise ValueError('Accepted successor task identity changed')
            # Replay the original accepted route; controller._check_job still
            # verifies it exactly. Today's shares gate only new admission.
            return (*route, None)
    code = reason(root, step, job, result, role, correction_request, allow_repeat=allow_repeat)
    if code is None:
        return (*route, None)
    # Insufficient allocation never authorizes another scientific task.
    return (*route, code)


def reconsider(root, step, job, route, code):
    """Return one refused optional choice to the existing finite H correction."""
    if code not in REFUSALS:
        raise ValueError('Unknown optional-node admission refusal')
    _, parents, _, _, current_h, current_w, _, _ = route
    from . import semantic
    source = semantic._source(root, step)
    previous = job.get('author_correction')
    request = dict(root=previous['root'] if previous is not None and previous['reason'] in REFUSALS else source,
        source=source, reason=previous['reason'] if previous is not None and previous['reason'] in REFUSALS else code)
    instruction = ('Control refused the optional scientific node: ' + code + '. '
        'Reconsider the scientific route from the retained evidence. Choose a justified alternative; '
        'no new evidence, budget, execution permission or renewed correction allowance is supplied.')
    return 'H', list(dict.fromkeys([*parents, step])), instruction, None, current_h, current_w, None, request
