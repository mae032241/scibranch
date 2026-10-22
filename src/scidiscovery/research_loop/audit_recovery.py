"""One pre-authorized control retry of a scratch-only audit, never promotion.

Every retry has an immutable intent/outcome and consumes the original shared
budget. An accepted retry without its outcome is unknown and cannot be replayed.
"""
from __future__ import annotations

import math
import re

from . import budget, contracts as c, configuration_updates

AUDIT_STATUSES = {'audited', 'rejected', 'engineering_error', 'unclassified', 'unknown'}
CONTROL_CODES = {'audit_source_missing', 'audit_harness_unavailable', 'audit_process_failed',
                 'audit_transport_timeout', 'audit_output_capacity', 'audit_process_unavailable'}


def intents(root):
    """Accepted retry declarations; outcome integrity is a separate result check."""
    for path in sorted((root / 'host-delivery/audit-recovery').glob('*/intent.json')):
        intent = c.read(c.regular(path))
        settings = configuration_updates.for_job(root, intent)
        if (intent.get('settings_sha256') != c.digest(root / 'settings.json')
                or intent.get('operation_id') != path.parent.name
                or type(intent.get('attempt')) is not int or intent['attempt'] != 1
                or intent.get('reason') not in CONTROL_CODES
                or not re.fullmatch('[0-9a-f]{64}',path.parent.name)
                or type(intent.get('max_seconds')) not in (int,float) or not math.isfinite(intent['max_seconds'])
                or not 0 < intent['max_seconds'] <= settings['delivery_policy']['submit_seconds']
                or type(intent.get('advance_seconds')) not in (int,float) or not math.isfinite(intent['advance_seconds'])
                or intent['advance_seconds']<0):
            raise ValueError('Audit engineering recovery identity changed')
        yield intent


def facts(root):
    for intent in intents(root):
        path = root/'host-delivery/audit-recovery'/intent['operation_id']/'intent.json'
        outcome = path.with_name('outcome.json')
        value = c.read(c.regular(outcome)) if outcome.exists() else None
        if value is not None:
            if (value['intent_sha256'] != c.digest(path) or value.get('recovery','none') not in {'none','author_repair','control_retry'}
                or value.get('status') not in AUDIT_STATUSES
                    or type(value.get('cleanup_confirmed')) is not bool
                    or not value['cleanup_confirmed'] and value['status']!='unknown'):
                raise ValueError('Audit engineering recovery outcome changed')
            process=path.with_name('process.json')
            if value.get('process_sha256') is not None:
                if c.digest(c.regular(process))!=value['process_sha256']:
                    raise ValueError('Audit engineering recovery process changed')
                record=c.read(process)
                if record.get('phase')!='audit' or record.get('identity',{}).get('task_sha256')!=intent['operation_id']:
                    raise ValueError('Audit engineering recovery process identity changed')
            elif value['status']=='audited':raise ValueError('A completed engineering retry needs its process original')
        yield intent, value


def blocked(root):
    return any(value is None or not value['cleanup_confirmed'] for _, value in facts(root))


def run(root, spec, source, request, seconds, *, on_process=None, author_required=True, job=None):
    from pathlib import Path
    import time
    from . import budget, delivery, native
    from .candidate_audit import _save
    deadline = time.clock_gettime(time.CLOCK_BOOTTIME) + seconds

    def call(item, callback):
        if deadline-time.clock_gettime(time.CLOCK_BOOTTIME)<=0:
            return dict(status='engineering_error',note='audit_transport_timeout',cleanup_confirmed=True)
        try:
            value = delivery._call(spec, dict(item, phase='audit'),
                max(0, deadline-time.clock_gettime(time.CLOCK_BOOTTIME)), on_process=callback,
                evidence=delivery.leaf_output(root))
        except delivery.DeliveryUnresolved as error:
            if error.code == 'delivery_cleanup_unknown':
                return dict(status='unknown', note=error.code, cleanup_confirmed=False)
            code = {'delivery_audit_timeout':'audit_transport_timeout',
                    'delivery_audit_capacity':'audit_output_capacity',
                    'delivery_audit_process_failed':'audit_process_failed','delivery_audit_process_unavailable':'audit_process_unavailable'}.get(error.code)
            if code is None:raise
            return dict(status='engineering_error', note=code, cleanup_confirmed=True)
        if (not isinstance(value, dict) or set(value)-{'diagnostic','recovery'} != {'status','operation_id','candidate_id','artifact_sha256','note'}
                or any(value.get(k) != request[k] for k in ('operation_id','candidate_id','artifact_sha256'))
                or value.get('recovery','none') not in {'none','author_repair','control_retry'}
                or value.get('status') not in AUDIT_STATUSES or not isinstance(value.get('note'), str)
                or len(value['note'].encode()) > 1024 or 'diagnostic' in value and (not isinstance(value['diagnostic'],str) or len(value['diagnostic'].encode())>4096)):
            return dict(status='unclassified', note='audit_receipt_invalid', cleanup_confirmed=True)
        return dict(status=value['status'], note=value['note'], cleanup_confirmed=True,recovery=value.get('recovery','none'),**(dict(diagnostic=value['diagnostic']) if 'diagnostic' in value else {}))

    directory = root / 'host-delivery/audit-recovery' / request['operation_id']
    intent_path, outcome_path = directory/'intent.json', directory/'outcome.json'
    if intent_path.exists():
        # The caller's original intent normally prevents this path on restart.
        # Still refuse to reconstruct a successful scratch audit for promotion.
        return dict(status='unknown', note='audit_recovery_already_accepted', cleanup_confirmed=False), request, deadline
    result = call(request, on_process)
    if result['status'] != 'engineering_error' or not result['cleanup_confirmed']:
        return result, request, deadline
    if result.get('recovery')=='author_repair':
        return result,request,deadline
    # Only framework transport failures have a pre-authorized control retry.
    # A provider declaration classifies diagnostics but cannot grant retries.
    if result['note'] not in CONTROL_CODES:
        return dict(status='unclassified',note='audit_engineering_reason_unclassified',cleanup_confirmed=True),request,deadline
    limits, meter = budget.snapshot(root), native.usage(root)
    settings = delivery._settings(root, job)
    policy=settings['delivery_policy']
    reserved_seconds=policy['cleanup_seconds']+policy['confirm_seconds']
    if author_required:reserved_seconds+=policy['closeout_a']['max_seconds']+policy['submit_seconds']
    remaining=min(policy['submit_seconds'],limits['seconds_remaining']-reserved_seconds)
    reserved_steps=3 if author_required else 1
    if (remaining <= 0 or meter.get('blocking_unknown_steps', meter['unknown_steps']) or meter.get('accounted_tokens', meter['observed_tokens']) >= limits['max_tokens']
            or budget.used_steps(root)+1+reserved_steps > budget.effective_max_steps(root)):
        return result, request, deadline
    intent = dict(settings_sha256=c.digest(root/'settings.json'), operation_id=request['operation_id'], attempt=1,
        candidate_id=request['candidate_id'], artifact_sha256=request['artifact_sha256'],
        object_digests=request['object_digests'], reason=result['note'], max_seconds=min(remaining,limits['seconds_remaining']),
        advance_seconds=limits['advance_seconds'])
    if job is not None and job.get('configuration_revision') is not None:
        intent['configuration_revision'] = job['configuration_revision']
    _save(intent_path, intent)
    deadline=time.clock_gettime(time.CLOCK_BOOTTIME)+intent['max_seconds']
    fresh = delivery._staged(root, spec, source, request['operation_id'], Path(request['stage'])/'recovery-1', job=job)
    def process(phase, identity):
        _save(directory/'process.json', dict(phase=phase, identity=identity))
    result = call(fresh, process)
    _save(outcome_path, dict(intent_sha256=c.digest(intent_path),process_sha256=c.digest(directory/'process.json') if (directory/'process.json').exists() else None, **result))
    return result, fresh, deadline
