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
    return (job.get('role') == 'A' and kind(job) in {'ordinary', 'delivery_review', 'closeout'}
        and not diagnostic_advice(job))


def advisory_scope(job):
    """Frozen scope marker, independent of the automatic trigger policy."""
    return (type(job.get('research_strategy_version')) is int and job['research_strategy_version'] == 3
        and type(job.get('advisory_scope_version')) is int and job['advisory_scope_version'] == 1)


def diagnostic_advice(job):
    return job.get('role') == 'A' and kind(job) in {'ordinary', 'consultation'} and advisory_scope(job)


def terminal_finish(job):
    """Only these sealed finishes can be a result without a closeout source."""
    return (job.get('role') == 'A' and (kind(job) == 'delivery_review'
        or kind(job) == 'ordinary' and job.get('research_strategy_version') not in (2, 3)))


def role_label(job):
    role = job.get('role')
    if diagnostic_advice(job):
        if reflection(job) is not None:
            return 'A · W 咨询与自动诊断建议' if kind(job) == 'consultation' else 'A · 自动诊断建议（返回 W）'
        return 'A · W 研究咨询' if kind(job) == 'consultation' else 'A · 研究诊断建议（返回 W）'
    if role == 'E' and kind(job) == 'consultation' and advisory_scope(job):
        return 'E · W 证据咨询'
    if reflection(job) is not None:
        return 'A · 自动研究反思'
    return {'consultation': role + ' · W 研究咨询', 'formal_review': 'A · 正式评审',
        'delivery_review': 'A · 提交前最终评估', 'delivery_correction': 'W · 提交前有界修正',
        'engineering_repair': role + ' · 工程维修', 'closeout': role + ' · 最终评估'}.get(kind(job))


def reflection(job):
    """Allowlisted frozen request facts, with no inferred scientific verdict."""
    value = job.get('supervision_request')
    merged = diagnostic_advice(job) and kind(job) == 'consultation'
    if merged:
        requester = job.get('evidence_return')
        if (not isinstance(requester, dict) or requester.get('role') != 'W'
                or type(requester.get('step')) is not int or type(job.get('step')) is not int
                or not 0 < requester['step'] < job['step']):
            return None
    if (job.get('role') != 'A' or (kind(job) != 'ordinary' and not merged)
            or not isinstance(value, dict) or type(value.get('version')) is not int
            or value['version'] != 1 or value.get('review_kind') != 'ordinary_reflection'
            or value.get('reflection_scope') != 'worker_question_clues_evidence'
            or value.get('automatic') is not True or value.get('final') is not False):
        return None
    adaptive = bool({'policy_version', 'source_w', 'budget_scope', 'adaptive_cutoff'} & set(value))
    if adaptive:
        # Validate the complete frozen scope, then project scalars explicitly.
        # The private raw budget scope is never a display value.
        if (type(value.get('policy_version')) is not int or value['policy_version'] != 4
                or type(job.get('research_strategy_version')) is not int
                or job['research_strategy_version'] != 3 or type(job.get('step')) is not int):
            return None
        from . import contracts
        try:
            contracts.supervision_binding(value, step=job['step'], research_strategy_version=3,
                supervision_policy_version=4)
        except (ValueError, TypeError, OverflowError):
            return None
    reasons = []
    for reason in value.get('due_reasons', []) if isinstance(value.get('due_reasons'), list) else []:
        if not isinstance(reason, str):
            continue
        if adaptive:
            if reason in {'changed_authored_question_or_original_receipt', 'operational_no_new_experiment'}:
                reasons.append(reason)
        elif reason in {'consecutive_calls', 'consecutive_rounds'}:
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
    if diagnostic_advice(job):
        result['advisory_scope_version'] = 1
    if type(value.get('investment_cutoff')) is int and value['investment_cutoff'] >= 0:
        result['investment_cutoff'] = value['investment_cutoff']
    if adaptive:
        result.update(policy_version=4, source_w=value['source_w'],
            question_revision=value['adaptive_cutoff']['question_revision'],
            receipt_cutoff=value['adaptive_cutoff']['receipts'])
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
        if job.get('role') == 'E' and advisory_scope(job):
            return '证据答复后返回原 W；由 W 判断后续研究'
        return '咨询答复后返回原 W；本建议不决定路由'
    if diagnostic_advice(job) and value == 'continue_w':
        return '诊断建议返回 W；由 W 自主决定后续研究'
    if (job.get('role') == 'A' and category == 'ordinary'
            and job.get('research_strategy_version') in (2, 3) and value in {'finish', 'deliver'}):
        return '请求提交前最终评估'
    return labels.get(value, value)
