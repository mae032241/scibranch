"""Frozen role token shares derived from sealed meters; routing only, no new balance.

Optional H/A thinking is refused once its reported exploration usage reaches
the frozen share, or when
the same role repeats without new sealed W/E evidence or an audit receipt.
Required reviews stay legal: A after W, A after an audit receipt, control
correction review and closeout.
"""
from __future__ import annotations

from . import contracts as c, execution

DEFAULT = dict(version=1, min_w_share=.65, max_thinking_share=.35, thinking_node_target_tokens=250_000)
THINKING = ('H', 'A')


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
    """Sum original meters of steps 1..through_step only, so replay is exact."""
    totals = dict(H=0, W=0, A=0, E=0, closeout=0)
    unknown = []
    for step in range(1, through_step + 1):
        path = root / 'jobs' / f'{step:03d}.json'
        if not path.exists():
            break
        job = c.read(c.regular(path))
        key = 'closeout' if job.get('purpose') == 'closeout' else job['role']
        for record in execution.records(root, step):
            if not (record / 'native-intent.json').exists():
                continue
            if not (record / 'usage.json').exists():
                unknown.append(step)
                continue
            meter = c.read(c.regular(record / 'usage.json'))
            totals[key] += meter['input_tokens'] + meter['output_tokens']
    return dict(totals, thinking=totals['H'] + totals['A'], unknown_steps=sorted(set(unknown)))


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
    meter, total = usage(root, step), c.settings(root, job)['max_tokens']
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
    if author is not None:
        return (*route, None)
    code = reason(root, step, job, result, role, correction_request, allow_repeat=allow_repeat)
    if code is None:
        return (*route, None)
    # Insufficient allocation never authorizes another scientific task.
    return None, [], '', None, current_h, current_w, None, None, code
