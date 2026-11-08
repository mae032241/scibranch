"""Pure display meanings of frozen task authority; never a routing policy."""
import math

from . import delivery_review


def kind(job):
    if job.get('engineering_repair'):
        return 'engineering_repair'
    if job.get('purpose') == 'worker_consultation':
        return 'consultation'
    if job.get('formal_review') or job.get('purpose') == 'formal_review':
        return 'formal_review'
    scope = delivery_review.scope(job)
    if scope == 'delivery_review':
        return 'delivery_review'
    if scope == 'delivery_correction':
        return 'delivery_correction'
    if job.get('purpose') == 'closeout' or job.get('final_report') or job.get('final_only'):
        return 'closeout'
    return 'ordinary'


def assessment(job):
    return job.get('role') == 'A' and kind(job) in {'ordinary', 'delivery_review', 'closeout'}


def terminal_finish(job):
    """Only these sealed finishes can be a result without a closeout source."""
    return (job.get('role') == 'A' and (kind(job) == 'delivery_review'
        or kind(job) == 'ordinary' and job.get('research_strategy_version') not in (2, 3)))


def role_label(job):
    role = job.get('role')
    if reflection(job) is not None:
        return 'A · 自动研究反思'
    return {'consultation': role + ' · W 研究咨询', 'formal_review': 'A · 正式评审',
        'delivery_review': 'A · 提交前最终评估', 'delivery_correction': 'W · 提交前有界修正',
        'engineering_repair': role + ' · 工程维修', 'closeout': role + ' · 最终评估'}.get(kind(job))


def reflection(job):
    """Allowlisted frozen request facts, with no inferred scientific verdict."""
    value = job.get('supervision_request')
    if (job.get('role') != 'A' or kind(job) != 'ordinary'
            or not isinstance(value, dict) or type(value.get('version')) is not int
            or value['version'] != 1 or value.get('review_kind') != 'ordinary_reflection'
            or value.get('reflection_scope') != 'worker_question_clues_evidence'
            or value.get('automatic') is not True or value.get('final') is not False):
        return None
    reasons = []
    for reason in value.get('due_reasons', []) if isinstance(value.get('due_reasons'), list) else []:
        if not isinstance(reason, str):
            continue
        if reason in {'consecutive_calls', 'consecutive_rounds'}:
            reasons.append(reason)
        elif isinstance(reason, str) and reason.startswith('budget_checkpoint:'):
            try:
                point = float(reason.split(':', 1)[1])
            except ValueError:
                continue
            if math.isfinite(point) and 0 < point <= 1:
                reasons.append('budget_checkpoint:' + str(point))
    fraction = value.get('budget_fraction')
    try:
        valid_fraction = type(fraction) in (int, float) and math.isfinite(fraction) and fraction >= 0
    except OverflowError:
        valid_fraction = False
    if not valid_fraction:
        fraction = None
    result = dict(due_reasons=reasons, budget_fraction=fraction)
    if type(value.get('investment_cutoff')) is int and value['investment_cutoff'] >= 0:
        result['investment_cutoff'] = value['investment_cutoff']
    return result


def worker_continuation(job, outcome):
    """One public continuation observation, correlated to the frozen W tuple."""
    value = outcome.get('worker_continuation') if isinstance(outcome, dict) else None
    frozen = job.get('worker_continuation')
    if (job.get('role') != 'W' or type(job.get('worker_continuity_version')) is not int
            or job['worker_continuity_version'] != 1 or not isinstance(frozen,dict)
            or type(frozen.get('version')) is not int or frozen['version'] != 1
            or not isinstance(value, dict)
            or type(value.get('version')) is not int or value['version'] != 1
            or 'fallback_reason' not in value
            or type(job.get('step')) is not int
            or type(value.get('requester_step')) is not int
            or type(value.get('advice_step')) is not int
            or not 0 < value['requester_step'] < value['advice_step'] == job['step'] - 1
            or type(frozen.get('requester_step')) is not int or type(frozen.get('advice_step')) is not int
            or frozen['requester_step'] != value['requester_step'] or frozen['advice_step'] != value['advice_step']
            or not isinstance(value.get('mode'),str)
            or value['mode'] not in {'same_session', 'new_context', 'resume_unconfirmed'}):
        return None
    reason = value.get('fallback_reason')
    if value['mode'] == 'same_session' and reason is not None:
        return None
    allowed = {'consultation_resume_' + suffix for suffix in ('unsupported_backend', 'profile_changed',
        'backend_changed', 'session_unavailable', 'shared_home_unavailable', 'context_rotation_required',
        'context_replaced', 'inherited_source')}
    return dict(version=1, requester_step=value['requester_step'], advice_step=value['advice_step'],
        mode=value['mode'], fallback_reason=reason if isinstance(reason, str) and reason in allowed else None)


def action(job, value, labels):
    category = kind(job)
    if category == 'consultation':
        return '咨询答复后返回原 W；本建议不决定路由'
    if (job.get('role') == 'A' and category == 'ordinary'
            and job.get('research_strategy_version') in (2, 3) and value in {'finish', 'deliver'}):
        return '请求提交前最终评估'
    return labels.get(value, value)
