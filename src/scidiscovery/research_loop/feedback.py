"""Replaceable read-only progress. Never a balance, qualification or permission."""
from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess

from . import contracts as c, execution, history

VERSION = 1
MAX_CALLS = 16
MAX_BYTES = 16_000


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def budget_hint(work):
    """Read only the host's fixed work/budget-status.json; unknown is never zero."""
    work = Path(work)
    path = work / 'work/budget-status.json'
    unavailable = dict(status='unavailable', observed_at=None, inflight_usage_unknown=True, review_request=None)
    try:
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise history.ObserverIntegrityError('Budget feedback path is not the fixed original')
        if not path.exists():
            return unavailable
        with c.regular(path).open('rb') as stream:
            data = stream.read(4097)
    except history.ObserverIntegrityError:
        raise
    except (OSError, ValueError) as error:
        raise history.ObserverIntegrityError('Budget feedback original cannot be read') from error
    if len(data) > 4096:
        return unavailable
    try:
        value = json.loads(data.decode('utf-8'))
    except (ValueError, RecursionError):
        return unavailable
    if not isinstance(value, dict) or value.get('scope') != 'advice_only_not_authorization_or_scientific_evidence':
        return unavailable
    fields = ('updated_at_unix', 'shared_observed_tokens_remaining_upper_bound', 'shared_active_seconds_remaining',
              'current_work_observed_tokens_remaining_upper_bound', 'current_work_active_seconds_available')
    final = (value.get('current_work_boundary') == 'final_a_return'
             and value.get('final_response_allowance') == 'original_final_a_token_overrun_authorized'
             and value.get('target_kind') == 'final_a_token_target')
    if any(not (final and key == fields[3] and key in value and value[key] is None)
           and (not _finite(value.get(key)) or value[key] < 0) for key in fields):
        return unavailable
    target, reserve = value.get('target_state'), value.get('reserve_state')
    if not isinstance(target, str) or not isinstance(reserve, str) or target not in {'within', 'near', 'reached'} or reserve not in {'within', 'reached'}:
        return unavailable
    reason = 'reserve_reached' if reserve == 'reached' else 'target_' + target if target != 'within' else None
    result = dict(status='observed_advice', observed_at=value['updated_at_unix'], inflight_usage_unknown=True,
        shared_observed_tokens_remaining_upper_bound=value[fields[1]], shared_active_seconds_remaining=value[fields[2]],
        current_work_observed_tokens_remaining_upper_bound=value[fields[3]], current_work_active_seconds_available=value[fields[4]],
        target_state=target, reserve_state=reserve,
        review_request=dict(requested=bool(reason), reason=reason, enforced=False),
        scope='Last host observation only; in-flight usage and current balance are unknown. Not execution authorization.')
    if final:
        result.update(current_work_boundary=value['current_work_boundary'],
                      final_response_allowance=value['final_response_allowance'], target_kind=value['target_kind'])
    from . import budget_notice
    result['notification_channel']=budget_notice.public_channel(value.get('notification_channel'))
    return result


def live(root, job):
    """Reuse the frozen observer without sealing, archiving or taking the writer lock."""
    root = Path(root)
    advisory = budget_hint(c.workspace(root, job['step']))
    result = dict(version=VERSION, status='unavailable', calls=[], counts=None, omitted_count=None,
        coverage='unavailable', observed_at=None, budget=advisory,
        limitation='Budget notification capability is reported separately; advice is also available on explicit query. Unmanaged work is not covered.')
    config = c.settings(root).get('history_observer')
    registered=job.get('experiment_interface_version')==1
    if job.get('live_feedback_version') != VERSION or (not registered and (config is None or config.get('contract_version') != 2)):
        return result
    chains = execution.records(root, job['step'])
    intent_path = chains[-1] / 'native-intent.json'
    if not intent_path.exists():
        return result
    task = c.record_ref(root / 'jobs' / f'{job["step"]:03d}.json')
    try:
        intent = c.read(c.regular(intent_path))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return result
    except (OSError, ValueError, RecursionError) as error:
        raise history.ObserverIntegrityError('Live feedback original intent cannot be read') from error
    if not isinstance(intent, dict) or intent.get('task_ref') != task:
        raise history.ObserverIntegrityError('Live feedback original task identity changed')
    keys = [execution.identity(root, job['step'], attempt) for attempt in range(len(chains))]
    if not keys:
        return result
    scope = dict(step=job['step'], role=job['role'], job_ref=task,
        execution_keys=keys, mechanism_ref=None if (job.get('search') or {}).get('breadth') else ((job.get('search') or {}).get('focus') or {}).get('mechanism_ref'))
    request = dict(progress_version=VERSION, root=str(root), step=job['step'], scope=scope,
        config=config['config'] if not registered else None)
    try:
        if registered:
            from . import experiments
            path=chains[-1]/'experiment-config.json'
            if not path.exists():return result
            frozen=c.read(c.regular(path))
            bound=frozen['scope']
            if (bound['step']!=scope['step'] or bound['role']!=scope['role'] or bound['job_ref']!=task
                    or bound['execution_key'] not in keys or bound['mechanism_ref']!=scope['mechanism_ref']):
                raise history.ObserverIntegrityError('Registered experiment observation escaped its frozen task')
            try:
                value=dict(experiments.progress(frozen),scope=scope)
                result['review']=experiments.review_feedback(frozen)
            except experiments.IntegrityError as error:raise history.ObserverIntegrityError(str(error)) from error
        else:value = history.read_adapter(config, request)
        expected = {'version', 'scope', 'calls', 'counts', 'omitted_count', 'coverage', 'observed_at'}
        if not isinstance(value, dict) or set(value) != expected or value['version'] != VERSION:
            raise ValueError('Progress component does not support its frozen contract')
        if value['scope'] != scope:
            raise history.ObserverIntegrityError('Live feedback escaped the accepted task')
        if (not isinstance(value['calls'], list) or len(value['calls']) > MAX_CALLS
                or type(value['omitted_count']) is not int or value['omitted_count'] < 0
                or value['coverage'] not in {'complete', 'partial', 'unavailable'}
                or not _finite(value['observed_at'])):
            raise ValueError('Progress observation is not bounded')
        names = {'accepted', 'running', 'known_success', 'known_failed', 'unknown'}
        if registered:
            names |= {'submitted', 'queued', 'cancelling', 'not_submitted'}
        counts = value['counts']
        if not isinstance(counts, dict) or set(counts) != names or any(type(n) is not int or n < 0 for n in counts.values()):
            raise ValueError('Progress counts require exact observed states')
        if sum(counts.values()) != len(value['calls']) + value['omitted_count']:
            raise ValueError('Progress omissions do not reconcile')
        ids = set()
        allowed = {'call_id', 'status', 'candidate_id', 'evaluation_key', 'metric', 'direction', 'wall_seconds', 'parent_candidate'}
        shapes = (allowed, allowed | {'mechanism_ref'})
        if registered:
            shapes += tuple(fields | {'returncode', 'result'} for fields in shapes)
            shapes += tuple(fields | {'native_state'} for fields in shapes)
        for row in value['calls']:
            retired = (registered and isinstance(row, dict) and set(row) == {'call_id', 'status', 'candidate_id'}
                and row['status'] == 'not_submitted' and row['candidate_id'] is None)
            if (not isinstance(row, dict) or (not retired and (set(row) not in shapes or row['status'] == 'not_submitted')) or row['status'] not in names
                    or not isinstance(row['call_id'], str) or not 0 < len(row['call_id']) <= 128 or row['call_id'] in ids):
                raise ValueError('Progress call identities are not unique')
            ids.add(row['call_id'])
            if retired:
                continue
            if 'native_state' in row and (row['native_state'] != 'cancelled' or row['status'] != 'known_failed'):
                raise ValueError('Registered cancellation observation is malformed')
            if 'result' in row and (not registered or row['returncode'] is not None and type(row['returncode']) is not int
                    or not isinstance(row['result'],dict)):
                raise ValueError('Registered result observation is malformed')
            if 'mechanism_ref' in row:
                if not isinstance(row['mechanism_ref'],str):
                    raise history.ObserverIntegrityError('Progress mechanism identity is malformed')
                # Registered progress already consumes Records.accepted's exact
                # worker/breadth binding; only the adapter projection needs H membership.
                if not registered and row['mechanism_ref'] not in {item['mechanism_ref'] for item in (job.get('search') or {}).get('breadth',{}).get('mechanisms',[])}:
                    raise history.ObserverIntegrityError('Progress mechanism escaped frozen H combination')
            if any(row[key] is not None and (not isinstance(row[key], str) or len(row[key].encode()) > 80)
                   for key in ('candidate_id', 'evaluation_key', 'parent_candidate')):
                raise ValueError('Progress text exceeds its bound')
            if (row['direction'] not in {None, 'max', 'min'} or any(row[key] is not None and
                    not _finite(row[key]) for key in ('metric', 'wall_seconds'))):
                raise ValueError('Progress metrics require finite public observations')
        if len(history._bytes(value)) > MAX_BYTES:
            raise ValueError('Progress observation exceeds byte bound')
    except history.ObserverIntegrityError:
        raise
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
        return result
    result.update(status='observed', **{key: value[key] for key in ('calls', 'counts', 'omitted_count', 'coverage', 'observed_at')})
    result['qualification'] = 'Completion metadata only; source/model integrity, audit and target suitability are not established.'
    return result
