"""Pure display meanings of frozen task authority; never a routing policy."""
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
    return {'consultation': role + ' · W 研究咨询', 'formal_review': 'A · 正式评审',
        'delivery_review': 'A · 提交前最终评估', 'delivery_correction': 'W · 提交前有界修正',
        'engineering_repair': role + ' · 工程维修', 'closeout': role + ' · 最终评估'}.get(kind(job))


def action(job, value, labels):
    category = kind(job)
    if category == 'consultation':
        return '咨询答复后返回原 W；本建议不决定路由'
    if (job.get('role') == 'A' and category == 'ordinary'
            and job.get('research_strategy_version') in (2, 3) and value in {'finish', 'deliver'}):
        return '请求提交前最终评估'
    return labels.get(value, value)
