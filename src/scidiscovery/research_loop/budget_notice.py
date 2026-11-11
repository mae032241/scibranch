"""Bounded budget advice at a native tool boundary; never an execution grant.

This file is also the staged hook executable. Its hook path uses only stdlib,
one fixed current hint and one exclusive marker per logical node and notice.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import time

SCOPE = 'advice_only_not_authorization_or_scientific_evidence'
MAX_NOTICE_BYTES = 200
WORKER_PROGRESS_TEXT = ('W time handoff due. Reconcile in-flight originals; never replay unknown calls. '
    'Save partial results for a fresh W. Direction supervision follows ledger/control due state.')
WORKER_CONTEXT_TEXT = ('W context handoff due. Reconcile originals; never replay unknown calls. '
    'Save partial results for a fresh W. Direction supervision follows ledger/control due state. Native may overshoot.')
WORKER_CONTEXT_UNKNOWN_TEXT = ('W context observation lost; windows are a lower bound. Reconcile originals; '
    'never replay unknown calls. Save partial results for a fresh W. Direction supervision follows ledger/control due state.')
WORKER_CONTEXT_NEAR_TEXT = ('W final context window is at least 85% full. Prepare a safe checkpoint; reconcile originals, never replay unknown calls. '
    'Advice only; original permissions and count-based handoff still hold.')
WORKER_DIRECTION_TEXT = ('W direction review due. Finish/reconcile bounded existing work; never replay unknown calls. '
    'Save a checkpoint and return for independent A. No new work or scientific verdict; permissions hold.')
WORKER_REFLECTION_TEXT = ('W reflection opportunity: reconcile existing originals; never replay unknown calls. '
    'Save question, clues and evidence; return for independent A. No progress verdict; original permissions hold.')
WORKER_INVESTMENT_TEXT = ('Repeated calls under this mechanism: inspect retained evidence and choose continuation or H/A. '
    'Counts measure effort, not scientific stagnation. Original permissions and budgets hold.')
WORKER_CONTEXT_90_TEXT = ('W context >=90%: prepare a safe checkpoint; compaction may continue. '
    'Handoff only after current post-compaction occupancy >70%. Reconcile originals; never replay.')
WORKER_SHARED_TOKEN_NEAR_TEXT = ('Shared cumulative tokens >=90% of configured total. Preserve progress; '
    'continue useful work within original permissions/shared limits. No handoff required; unknown usage != 0.')
HELPER = '.scid-budget-notice.py'
EMPTY_CONFIG = '.scid-budget-notice-empty-config.toml'
VISIBLE = 'scid-budget-hook-visible-v1'
METADATA = '.native-metadata-'
UNAVAILABLE_REASONS = {'not_preflighted', 'unsupported_backend', 'undeclared_native_permissions',
    'unsupported_namespace', 'hook_preflight_unavailable', 'hook_trust_unqualified', 'helper_unavailable'}


def public_channel(value):
    if isinstance(value, dict) and value in (dict(status='available', mechanism='codex_post_tool_use'),
            dict(status='available', mechanism='pi_tool_result')):
        return dict(value, limitation='Next local tool boundary only; running model requests and hosted web cannot be interrupted.')
    reason = value.get('reason') if isinstance(value, dict) else None
    return dict(status='unavailable', reason=reason if reason in UNAVAILABLE_REASONS else 'not_preflighted')


def ordinary_worker(job, intent, *, author=False, final_return=False):
    """Identify ordinary W advice without changing an accepted scope or grant."""
    return (job.get('role') == 'W' and not author and not final_return
        and intent.get('continuation_kind') != 'author_closeout'
        and (intent.get('budget_slice') or {}).get('final_return') is None
        and not intent.get('output_repair') and not intent.get('work_repair')
        and not job.get('formal_review')
        and job.get('purpose') not in {'closeout', 'worker_consultation', 'delivery_review', 'delivery_correction'}
        and (job.get('evidence_return') or {}).get('kind') not in {'delivery_review', 'delivery_correction'}
        and not job.get('final_report') and not job.get('final_only'))


def closeout_notice(job, intent, *, dimension, target_reached, shared_reached, target_near=False,
        shared_risk=False, shared_expired=False, shared_exhausted=False, followup_tokens=None,
        author=False, final_return=False):
    """Render only already-loaded observations; unknown usage is never zero."""
    if dimension not in {'token', 'time'}:
        raise ValueError('Unknown budget advice dimension')
    ordinary_w = ordinary_worker(job, intent, author=author, final_return=final_return)
    if ordinary_w:
        # Accepted node anchors remain accounting facts. W's cumulative token
        # reminder is independent of node targets and suggested follow-up work.
        target_reached = target_near = False
        shared_risk = shared_risk and dimension == 'token'
        followup_tokens = None
    # Global exhaustion changes ordinary A's advice, not this dimension's trigger.
    if not (target_reached or target_near or shared_reached or shared_risk):
        return None
    cause = 'shared' if shared_reached or shared_risk else 'node'
    risk = dimension.capitalize()+' budget risk.'
    if intent.get('output_repair'):
        text = (risk+' Finish output repair now; keep science/plans/limits/future action. '
            'No work/rerun/replay; permissions hold; unknown usage != 0.')
    elif intent.get('work_repair'):
        text = (risk+' Finish accepted missing work or report gaps now. '
            'Keep original authorization/action; no expansion/unknown replay; unknown usage != 0.')
    elif job.get('formal_review'):
        text = (risk+' Finish this exact independent review now from existing evidence; retain gaps and verdict authority. '
            'No new work; unknown usage != 0.')
    elif author:
        text = (risk+' Return author-only JSON from existing results now; retain gaps. '
            'No new work/replay; permissions hold; unknown usage != 0.')
    elif final_return:
        text = (risk+' Complete accepted final return now from existing results; retain gaps. '
            'No extra FINAL/new work; permissions hold; unknown usage != 0.')
    elif job.get('purpose')=='worker_consultation':
        text=(risk+' Return bounded advice for the exact W request now; retain evidence gaps. '
            'No verdict/new work/grant; original permissions and shared budgets hold; unknown usage != 0.')
    elif (job.get('research_strategy_version') in {2,3}
            and (job.get('evidence_return') or {}).get('kind') in {'delivery_review','delivery_correction'}):
        # This module is also a stdlib-only staged executable. The producer
        # supplies the already-validated frozen task binding, never a new grant.
        binding=job['evidence_return']
        exhausted=shared_expired or shared_reached or shared_exhausted
        if binding['kind']=='delivery_correction' and exhausted:
            cause='shared'
            text=(risk+' W: return retained correction/gaps for protected FINAL. '
                'No new work/replay; original permissions/budgets hold; unknown usage != 0.')
        elif binding['kind']=='delivery_correction':
            text=(risk+' W: save the exact correction/gaps; return for final reassessment. '
                'No scope expansion/replay; original permissions/budgets hold; unknown usage != 0.')
        elif exhausted:
            cause='shared'
            text=(risk+' A: conclude from originals; exact deliver or reject all with finish. '
                'No correction/new W/E/extra FINAL; permissions hold; unknown usage != 0.')
        elif binding.get('corrections_used')==1:
            text=(risk+' A: accept exact deliver or reject all with finish. No second correction/new work; '
                'permissions hold; unknown usage != 0.')
        else:
            text=(risk+' A: deliver, reject all with finish, or request one concrete correction in remaining budgets. '
                'No grant; permissions hold; unknown usage != 0.')
    elif (job['role']=='A' and job.get('advisory_scope_version')==1
            and job.get('research_strategy_version')==3 and not any(job.get(key) for key in
                ('purpose','final_report','final_only','engineering_repair','author_correction','correction_request'))):
        text=(risk+' A: return advice with continue_w and decision_request=null. W judges it; no direct work or finalization. '
            'Shared limits and original permissions hold; unknown usage != 0.')
    elif job['role'] == 'A' and not (job.get('purpose') == 'closeout' or job.get('final_report') or job.get('final_only')):
        if shared_expired or shared_reached or shared_exhausted:
            cause = 'shared'
            timing = ('Shared time expired.' if shared_expired else
                f'Shared {dimension} budget reached.' if shared_reached else risk+' Shared budget exhausted.')
            if job.get('research_strategy_version') in {1,2,3}:
                text=(timing+' A: conclude now; deliver/finish requests protected Final A. '
                    'No new H/W/E; permissions hold; unknown usage != 0.')
            else:
                text = (timing+' A: conclude now; deliver an eligible existing candidate or finish with gaps. '
                    'No H/W/E/extra FINAL; permissions hold; unknown usage != 0.')
        else:
            text = (risk+' A: end this assessment; retain unmet goals. '
                'Choose normal next_action by evidence/shared balance. '
                'Soft targets do not end research; permissions hold; unknown usage != 0.')
    elif ordinary_w and dimension == 'token' and shared_risk and not shared_reached:
        text = WORKER_SHARED_TOKEN_NEAR_TEXT
    elif (job['role'] == 'W' and not (job.get('purpose') == 'closeout'
            or job.get('final_report') or job.get('final_only'))
            and not (shared_reached or shared_risk or shared_expired or shared_exhausted)):
        text = (risk+' W: retain progress/best/residuals; continue useful work until safe handoff is due. '
            'Soft token targets alone do not require A; permissions hold; unknown usage != 0.')
    elif followup_tokens:
        roles = '+'.join(followup_tokens)
        text = (risk+f' Wrap up for suggested {roles} token targets. '
            'No reservation/grant; return JSON with gaps; permissions hold; unknown usage != 0.')
    elif shared_reached:
        text = (f'Shared {dimension} budget reached. Return scientific JSON from existing results now; retain gaps. '
            'No new work/replay; permissions hold; unknown usage != 0.')
    else:
        text = (risk+' Finish bounded actions and return scientific JSON now with limits. '
            'Soft target; permissions hold; unknown usage != 0.')
    if len(text.encode('utf-8')) > MAX_NOTICE_BYTES:
        raise ValueError('Budget closeout notice exceeds its fixed response allowance')
    return dict(cause=cause, text=text)


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _worker_progress_eligible(job, intent, *, fragment_enabled=False, author=False, final_return=False):
    """Preserve the accepted cadence scope independently of its observations."""
    return (fragment_enabled and job.get('role') == 'W' and not author and not final_return
        and intent.get('continuation_kind') != 'author_closeout'
        and (intent.get('budget_slice') or {}).get('final_return') is None
        and not job.get('formal_review') and job.get('purpose') != 'closeout'
        and not job.get('final_report') and not job.get('final_only'))


def worker_progress_due(job, intent, *, active_seconds, target_seconds, fragment_enabled=False,
        author=False, final_return=False, max_context_windows=None, context_observation=None, context_handoff_version=None):
    """Use loaded cadence and scope only; never infer scientific progress."""
    if not _worker_progress_eligible(job, intent, fragment_enabled=fragment_enabled,
            author=author, final_return=final_return):
        return False
    if max_context_windows is not None:
        return worker_context_reason(max_context_windows, context_observation,version=context_handoff_version) is not None
    return False  # Missing context telemetry never creates an elapsed-time loop.


def worker_context_reason(maximum, observation, *, version=None):
    """One initial window plus completed native compactions; no fullness scan."""
    if version==2:return worker_context_status(maximum,observation,version=version)['reason']
    observation = observation if isinstance(observation, dict) else {}
    windows = observation.get('context_windows') or {}
    started = windows.get('windows_started')
    if type(started) is int and started > maximum:
        return 'worker_context_review_due'
    if windows.get('reason') == 'context_capability_unavailable':
        return None  # Optional provider telemetry cannot schedule fresh W loops.
    if windows.get('coverage') == 'unknown':
        return 'worker_context_observation_unknown'
    if windows.get('coverage') != 'live' or type(started) is not int:
        return None  # The first native session/report has not arrived yet.
    return None


def worker_progress_notice(job, intent, **facts):
    """Render the already-loaded logical W review cadence, never a work grant."""
    if intent.get('output_repair') or intent.get('work_repair'):
        return None
    due = worker_progress_due(job, intent, **facts)
    if not due:
        # Eligibility is independent of a handoff trigger: a 90% checkpoint
        # notice remains valid before post-compaction handoff becomes due.
        if (not _worker_progress_eligible(job, intent, fragment_enabled=facts.get('fragment_enabled', False),
                    author=facts.get('author', False),
                    final_return=facts.get('final_return', False))
                or not worker_context_near(facts.get('max_context_windows'), facts.get('context_observation'),version=facts.get('context_handoff_version'))):
            return None
        return dict(cause='node', text=WORKER_CONTEXT_90_TEXT if facts.get('context_handoff_version')==2 else WORKER_CONTEXT_NEAR_TEXT)
    reason = worker_context_reason(facts['max_context_windows'], facts.get('context_observation'),version=facts.get('context_handoff_version')) if facts.get('max_context_windows') is not None else None
    return dict(cause='node', text=(WORKER_CONTEXT_UNKNOWN_TEXT if reason == 'worker_context_observation_unknown'
        else WORKER_CONTEXT_TEXT if reason else WORKER_PROGRESS_TEXT))


def worker_context_near(maximum, observation, *, version=None):
    """Optional last-window advice from current native occupancy, never billing."""
    if version==2:return worker_context_status(maximum,observation,version=version)['near']
    observation = observation if isinstance(observation, dict) else {}
    windows = observation.get('context_windows') or {}
    current = observation.get('context') or {}
    if not isinstance(windows, dict) or not isinstance(current, dict):
        return False
    used, capacity = current.get('total_tokens'), current.get('model_context_window')
    return (type(maximum) is int and maximum > 0
        and windows.get('coverage') == 'live' and type(windows.get('windows_started')) is int
        and windows['windows_started'] == maximum
        and current.get('status') == 'last_reported' and current.get('current_window') is True
        and _finite(current.get('observed_at'))
        and type(used) is int and 0 <= used <= 2 ** 53 - 1
        and type(capacity) is int and 0 < capacity <= 2 ** 53 - 1
        and used * 100 >= capacity * 85)


def worker_context_status(maximum, observation, *, version=None):
    """One pure snapshot fact shared by notices, admission and continuation.

    V2 allows ``maximum`` complete windows plus the next current window up to
    70% occupancy. A compaction count never supplies missing current occupancy.
    Native producers own current-window freshness; this consumer never scans.
    """
    if version in (None,1):
        return dict(reason=worker_context_reason(maximum,observation),
            near=worker_context_near(maximum,observation),ratio=None)
    if type(version) is not int or version!=2:
        raise ValueError('Unsupported context handoff version')
    observation=observation if isinstance(observation,dict) else {}
    windows=observation.get('context_windows') or {};current=observation.get('context') or {}
    started=windows.get('windows_started') if isinstance(windows,dict) else None
    used=current.get('total_tokens') if isinstance(current,dict) else None
    capacity=current.get('model_context_window') if isinstance(current,dict) else None
    valid=(type(maximum) is int and maximum>0 and isinstance(windows,dict)
        and windows.get('coverage')=='live' and type(started) is int and started>0
        and isinstance(current,dict) and current.get('status')=='last_reported'
        and current.get('current_window') is True and _finite(current.get('observed_at'))
        and type(used) is int and 0<=used<=2**53-1
        and type(capacity) is int and 0<capacity<=2**53-1)
    return dict(reason='worker_context_review_due' if valid and started>maximum and used*100>capacity*70 else None,
        near=bool(valid and started<=maximum and used*100>=capacity*90),
        ratio=used/capacity if valid else None)


def worker_direction_notice(review):
    """Render the producer's independent direction checkpoint, without reads."""
    if review is None:return None
    text=(WORKER_REFLECTION_TEXT if isinstance(review,dict)
        and review.get('review_kind')=='ordinary_reflection' else WORKER_DIRECTION_TEXT)
    return dict(cause='node',text=text)


def worker_investment_notice():
    """A bounded advisory checkpoint; it neither stops work nor requests A."""
    return dict(cause='node',text=WORKER_INVESTMENT_TEXT)


def published_notices(value):
    """Accept the bounded current host projection from its sole producer."""
    if not isinstance(value, dict) or value.get('scope') != SCOPE:
        return None
    if not _finite(value.get('updated_at_unix')) or value.get('inflight_usage_unknown') is not True:
        return None
    notices = value.get('closeout_notices')
    if not isinstance(notices, dict) or not notices or not set(notices).issubset({'token', 'time', 'progress', 'review','investment'}):
        return None
    # Shared risk also includes the producer's follow-up advice and time ratio.
    # The hook consumes that bounded projection; it does not reload settings or
    # reconstruct the producer's plan to decide whether advice is warranted.
    for dimension, notice in notices.items():
        if not isinstance(notice, dict) or set(notice) != {'cause', 'text'}:
            return None
        if notice.get('cause') not in {'node', 'shared'} or not isinstance(notice.get('text'), str):
            return None
        if len(notice['text'].encode('utf-8')) > MAX_NOTICE_BYTES:
            return None
        if notice['cause'] == 'node' and dimension!='investment' and value.get('target_state') not in {'near', 'reached'}:
            return None
        if dimension=='investment' and (notice!=worker_investment_notice()
                or not isinstance(value.get('research_investment'),dict)
                or value['research_investment'].get('mode')!='notice'
                or value['research_investment'].get('due') is not True):
            return None
        if dimension == 'review' and (notice != worker_direction_notice(value.get('direction_review'))
                or value.get('target_state') != 'reached'):
            # Historical hints carried only the original direction notice.
            if notice != dict(cause='node',text=WORKER_DIRECTION_TEXT) or value.get('target_state')!='reached':
                return None
        if dimension == 'progress':
            early = notice in [dict(cause='node', text=text)
                for text in (WORKER_CONTEXT_NEAR_TEXT, WORKER_CONTEXT_90_TEXT)]
            if (not early and (notice not in [dict(cause='node', text=text)
                    for text in (WORKER_PROGRESS_TEXT, WORKER_CONTEXT_TEXT, WORKER_CONTEXT_UNKNOWN_TEXT)]
                    or value.get('target_state') != 'reached')):
                return None
    return notices


def read_hint(work):
    """The hook's sole input read: fixed name, ordinary file, <=4096 bytes."""
    path = Path(work) / 'work/budget-status.json'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            data = stream.read(4097)
        if len(data) > 4096:
            return None
        return published_notices(json.loads(data.decode('utf-8')))
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None


def hook_output(work, attempt):
    notices = read_hint(work)
    if notices is None:
        return None
    directory = Path(work) / 'work'
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            for dimension in ('time', 'token', 'review', 'progress','investment'):
                if dimension not in notices:
                    continue
                try:
                    item = os.open('.budget-notice-'+dimension,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o400, dir_fd=fd)
                except FileExistsError:
                    continue
                os.close(item)
                return dict(hookSpecificOutput=dict(hookEventName='PostToolUse', additionalContext=notices[dimension]['text']))
        finally:
            os.close(fd)
    except OSError:
        return None
    return None


def metadata_path(work, attempt):
    return Path(work) / 'work' / (METADATA + str(attempt) + '.json')


def read_metadata(path):
    """Read the compact current hook projection, never its transcript pointer."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Native metadata is not an ordinary file')
        data = stream.read(16385)
    if len(data) > 16384:
        raise ValueError('Native metadata projection is invalid')
    value = json.loads(data)
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('Native metadata projection is invalid')
    return value


def _publish_metadata(path, value):
    temporary = path.with_name(path.name + '-' + os.urandom(8).hex())
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, separators=(',', ':'))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try: temporary.unlink()
        except FileNotFoundError: pass


def start_metadata(work, attempt, *, session=None):
    """Initialize once before this exact native submission; never reset a receipt."""
    path = metadata_path(work, attempt)
    if path.exists() or path.is_symlink():
        raise ValueError('Native metadata attempt already exists')
    value = dict(version=1, attempt=attempt, cwd=str(work), session_id=session,
        transcript_path=None, compactions=0, calls=dict(completed=0, failed=0, cancelled=0),
        tokens=dict(input_tokens=0, cached_input_tokens=0, output_tokens=0), reported_calls=0,
        usage_unknown=False, pending=False, pending_started_at=None, acknowledged=0, fault=None)
    _publish_metadata(path, value)
    return path


def record_metadata(work, attempt, event=None, *, reader=None):
    """One synchronous native delivery updates only compact invocation facts.

    No hook retry is configured. Native supplies no compaction event ID, so a
    replay cannot be invented or deduplicated by guessing from a turn ID.
    """
    path = metadata_path(work, attempt)
    lock = os.open(path.with_suffix('.lock'), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = read_metadata(path)
        if value.get('pending') is not False or value.get('fault') is not None:
            value['fault'] = 'native_metadata_delivery_incomplete'
            _publish_metadata(path, value)
            raise ValueError('Native metadata delivery is already incomplete')
        value['pending'] = True
        value['pending_started_at'] = time.monotonic()
        _publish_metadata(path, value)
        try:
            event = reader() if reader is not None else event
        except BaseException:
            value['fault'] = 'native_metadata_input_unavailable'
            _publish_metadata(path, value)
            raise
        valid = (isinstance(event, dict) and event.get('cwd') == str(work)
            and isinstance(event.get('session_id'), str) and re.fullmatch(r'[A-Za-z0-9_-]{1,256}', event['session_id'])
            and value.get('attempt') == attempt and value.get('cwd') == str(work)
            and value.get('session_id') in (None, event['session_id']))
        if not valid:
            value['fault'] = 'native_metadata_binding_invalid'
        else:
            value['session_id'] = event['session_id']
            transcript = event.get('transcript_path')
            if transcript is not None:
                if (not isinstance(transcript, str) or not Path(transcript).is_absolute()
                        or value.get('transcript_path') not in (None, transcript)):
                    value['fault'] = 'native_metadata_binding_invalid'
                else:
                    value['transcript_path'] = transcript
            kind = event.get('hook_event_name')
            if kind == 'PostCompact' and event.get('trigger') in ('auto', 'manual'):
                value['compactions'] += 1
            elif kind == 'PostModelRequest' and event.get('status') in value['calls']:
                value['calls'][event['status']] += 1
                usage = event.get('token_usage')
                fields = ('input_tokens', 'cached_input_tokens', 'output_tokens')
                if (not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in fields)
                        or usage['cached_input_tokens'] > usage['input_tokens']):
                    value['usage_unknown'] = True
                else:
                    for key in fields: value['tokens'][key] += usage[key]
                    value['reported_calls'] += 1
            else:
                value['fault'] = 'native_metadata_event_invalid'
        value['pending'] = False
        value['pending_started_at'] = None
        value['acknowledged'] += 1
        _publish_metadata(path, value)
    finally:
        os.close(lock)


def start_delivery(path, *, source, nonce, cwd, attempt):
    """Private compact source seam; not a second usage counter or authority."""
    path = Path(path)
    if path.exists() or path.is_symlink(): raise ValueError('Metadata attempt already exists')
    _publish_metadata(path, dict(version=1, source=source, nonce=nonce, cwd=str(cwd),
        attempt=attempt, pending=False, pending_started_at=None, acknowledged=0,
        sequence=0, event=None, fault=None))


def update_delivery(path, nonce, sequence, *, fault=None):
    """Parent acknowledgement AFTER acceptance/publication, or a sticky fault."""
    path = Path(path)
    lock = os.open(path.with_suffix('.lock'), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = read_metadata(path)
        if value.get('nonce') != nonce or value.get('sequence') != sequence:
            raise ValueError('Metadata watermark changed')
        if fault is not None:
            value['fault'] = value.get('fault') or fault
        elif value.get('fault') is None and value.get('pending') is True:
            value.update(pending=False, pending_started_at=None, acknowledged=sequence)
        else:
            raise ValueError('Metadata cannot be acknowledged')
        _publish_metadata(path, value)
        return value
    finally:
        os.close(lock)


def deliver_metadata(path, nonce, event, *, timeout=5):
    """Native->parent two-phase receipt. NEVER wait while holding the lock.

    The source supplies a compact observation, not authorization. Acceptance and
    shared review publication belong to the parent. Timeouts cannot be repaired
    by a late acknowledgement or another invocation of this helper.
    """
    path = Path(path)
    lock = os.open(path.with_suffix('.lock'), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = read_metadata(path)
        if (value.get('nonce') != nonce or value.get('pending') is not False
                or value.get('fault') is not None or value.get('sequence') != value.get('acknowledged')):
            raise ValueError('Metadata delivery incomplete')
        value.update(sequence=value['sequence'] + 1, pending=True,
            pending_started_at=time.monotonic(), event=event)
        _publish_metadata(path, value)
        sequence = value['sequence']
    finally:
        os.close(lock)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = read_metadata(path)
        if current.get('nonce') != nonce or current.get('sequence') != sequence or current.get('fault'):
            raise ValueError('Metadata delivery failed')
        if current.get('acknowledged') == sequence and current.get('pending') is False:
            return
        time.sleep(.01)
    update_delivery(path, nonce, sequence, fault='native_metadata_delivery_timeout')
    raise ValueError('Metadata delivery timeout')


def definition(work, attempt, *, python=None):
    command = shlex.join([python or sys.executable, str(Path(work) / 'work' / HELPER),
        '--work', str(work), '--attempt', str(attempt)])
    def handler(argv, *, additional_context=False):
        hook = dict(type='command', command=argv, timeout=5, **{'async': False})
        # Metadata-only events cannot emit context; Codex warns even for zero.
        if additional_context:
            hook['additionalContextLimit'] = 0
        return [dict(hooks=[hook])]
    return dict(PostToolUse=handler(command, additional_context=True), PostModelRequest=handler(command + ' --metadata'),
        PostCompact=handler(command + ' --metadata'))


def stage_helper(work, *, empty_config=True):
    """Publish this small stdlib executable before readonly namespace assembly."""
    directory = Path(work) / 'work'
    destination = directory / HELPER
    temporary = directory / (HELPER + '-' + os.urandom(8).hex())
    try:
        source = Path(__file__).read_bytes()
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o400)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(source)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        if empty_config:
            empty = directory / EMPTY_CONFIG
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o400)
            os.close(fd)
            os.replace(temporary, empty)
        return destination
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def inventory(value, work):
    """Validate required definitions and trust; warnings alone grant no facts."""
    if not isinstance(value, dict) or set(value) != {'data'} or not isinstance(value['data'], list) or len(value['data']) != 1:
        raise ValueError('Hook inventory is unavailable')
    entry = value['data'][0]
    if (not isinstance(entry, dict) or set(entry) != {'cwd', 'hooks', 'warnings', 'errors'}
            or entry['cwd'] != str(work) or not isinstance(entry['warnings'], list)
            or len(entry['warnings']) > 128 or any(not isinstance(item,str) for item in entry['warnings'])
            or entry['errors'] != []
            or not isinstance(entry['hooks'], list) or len(entry['hooks']) > 128):
        raise ValueError('Hook inventory has errors or an unsupported shape')
    hooks = {}
    for hook in entry['hooks']:
        if (not isinstance(hook, dict) or not isinstance(hook.get('key'), str) or hook['key'] in hooks
                or type(hook.get('enabled')) is not bool or type(hook.get('isManaged')) is not bool
                or hook.get('trustStatus') not in {'trusted', 'managed', 'untrusted', 'modified'}
                or not isinstance(hook.get('currentHash'), str)
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', hook['currentHash'])):
            raise ValueError('Hook inventory is ambiguous')
        hooks[hook['key']] = hook
    return hooks


def validate_hook_trust(before, after, declared, work):
    """Only our entire hook may need the invocation-local trust bypass."""
    original, candidate = inventory(before, work), inventory(after, work)
    if any(candidate.get(key) != hook for key, hook in original.items()):
        raise ValueError('Existing native hooks changed')
    added = [hook for key, hook in candidate.items() if key not in original]
    expected = dict(PostToolUse=('post_tool_use', 'postToolUse'),
        PostModelRequest=('post_model_request', 'postModelRequest'), PostCompact=('post_compact', 'postCompact'))
    if len(added) != len(declared) or set(declared) != set(expected):
        raise ValueError('Internal hooks are absent or duplicated')
    own_keys = set()
    for name, (key_name, event_name) in expected.items():
        matches = [hook for hook in added if hook.get('eventName') == event_name]
        if len(matches) != 1:
            raise ValueError('Internal metadata hook is absent or duplicated')
        own = matches[0]
        handler = declared[name][0]['hooks'][0]
        if (own.get('source') != 'sessionFlags' or own.get('sourcePath') != '/<session-flags>/config.toml'
                or not re.fullmatch(r'/<session-flags>/config\.toml:' + key_name + r':[0-9]+:[0-9]+', own['key'])
                or own.get('matcher') is not None or own['enabled'] is not True or own['isManaged'] is not False
                or own['trustStatus'] not in {'untrusted', 'trusted'}
                or own.get('handlerType') != 'command' or own.get('command') != handler['command']
                or own.get('async', False) is not False or own.get('timeoutSec') != handler['timeout']
                or own.get('additionalContextLimit') != handler.get('additionalContextLimit')
                or own.get('statusMessage') is not None or own.get('pluginId') is not None
                or own.get('environmentId') is not None):
            raise ValueError('The internal hook definition did not qualify')
        own_keys.add(own['key'])
    for key, hook in candidate.items():
        if key in own_keys or not hook['enabled']:
            continue
        if hook['trustStatus'] != 'trusted' and not (hook['trustStatus'] == 'managed' and hook['isManaged']):
            raise ValueError('Another enabled hook is not already trusted or managed')
    return True


def list_hooks(argv, work, env, *, pass_fds=(), timeout=5):
    """Bounded metadata-only app-server session; never create a thread or turn."""
    process = subprocess.Popen(argv, cwd=work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True, pass_fds=pass_fds)
    pending = b''
    stderr_tail = b''
    total = 0
    initialized = False
    deadline = time.monotonic() + timeout
    def send(value):
        process.stdin.write((json.dumps(value, separators=(',', ':')) + '\n').encode())
        process.stdin.flush()
    try:
        send(dict(id=1, method='initialize', params=dict(clientInfo=dict(name='scid-hook-capability', version='1'),
            capabilities=dict(experimentalApi=True))))
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            while selector.get_map() and time.monotonic() < deadline:
                events = selector.select(max(0, deadline - time.monotonic()))
                if not events:
                    break
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 16384)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.fileobj is process.stderr:
                        stderr_tail = (stderr_tail + chunk)[-1024:]
                        continue
                    total += len(chunk)
                    if total > 131072:
                        raise ValueError('Native hook inventory exceeded its byte bound')
                    pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    message = json.loads(line)
                    if isinstance(message, dict) and 'error' in message:
                        error=message['error']
                        detail=({key:error[key] for key in ('code','message') if key in error}
                            if isinstance(error,dict) else {'message':'Malformed native RPC error'})
                        raise ValueError('Native hook inventory RPC error: '+json.dumps(detail,ensure_ascii=False)[:768])
                    if not isinstance(message, dict):
                        raise ValueError('Native hook inventory RPC failed')
                    if message.get('id') == 1 and not initialized and 'result' in message:
                        initialized = True
                        send(dict(method='initialized'))
                        send(dict(id=2, method='hooks/list', params=dict(cwds=[str(work)])))
                    elif message.get('id') == 2 and initialized and 'result' in message:
                        return message['result']
                    elif 'id' in message:
                        raise ValueError('Unexpected native hook inventory RPC')
        raise ValueError('Native hook inventory timed out or ended early; stderr='
            +stderr_tail.decode('utf-8',errors='replace'))
    finally:
        if process.stdin is not None:
            process.stdin.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=1)
        process.stdout.close()
        process.stderr.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--attempt', type=int, required=True)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--metadata', action='store_true')
    parser.add_argument('--delivery-path', type=Path)
    parser.add_argument('--nonce')
    args = parser.parse_args(argv)
    if not args.work.is_absolute() or not 0 <= args.attempt <= 1_000_000:
        return 0
    if args.check:
        # No scientific/control record reads and no side effects in qualification.
        if (not Path(__file__).is_symlink() and Path(__file__).is_file()
                and not stat.S_IMODE(Path(__file__).stat().st_mode) & 0o222
                and (args.work / 'work/budget-status.json').is_file()):
            print(VISIBLE)
        return 0
    if args.delivery_path is not None:
        try:
            data = sys.stdin.buffer.read(8193)
            if len(data) > 8192: return 1
            deliver_metadata(args.delivery_path, args.nonce, json.loads(data))
        except (OSError, ValueError, TypeError, KeyError):
            return 1
        return 0
    if args.metadata:
        try:
            record_metadata(args.work, args.attempt, reader=lambda: json.load(sys.stdin))
        except (OSError, ValueError, TypeError, KeyError):
            return 1  # Preserve failed delivery; no resubmission or inferred count.
        return 0
    output = hook_output(args.work, args.attempt)
    if output is not None:
        print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
