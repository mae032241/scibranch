"""Optional engineering feedback on a confirmed call, never scientific qualification."""
from __future__ import annotations

import json
import time

from scidiscovery.operations.execution import AdapterCapability
from . import budget, experiments as e

KEY = 'isolated_replay'
INTENT = 'engineering-check.intent.json'
OUTCOME = 'engineering-check.outcome.json'
STATES = {'sample_replay_ok', 'dependency_error', 'inconclusive', 'unsupported', 'not_run', 'unknown'}


def reserve_seconds(config):
    """An operator-selected share of the existing call window, never a new grant."""
    seconds = config.get('engineering_check_reserve_seconds', 0)
    if type(seconds) not in (int, float) or not 0 <= seconds < float('inf'):
        raise ValueError('Engineering check reserve must be finite nonnegative seconds')
    return seconds


def acceptance(config, adapter):
    """Bind an optional declaration and the original call's deadline before submit."""
    matches = [item for item in adapter.capabilities() if item.key == KEY]
    if not matches:
        return dict(status='unsupported', note='optional_isolated_replay_not_declared')
    if len(matches) != 1 or not isinstance(matches[0], AdapterCapability):
        raise e.IntegrityError('Ambiguous engineering replay capability')
    item = matches[0]
    policy = json.loads(item.content)
    if (item.kind != 'engineering' or not callable(getattr(adapter, 'replay_check', None))
            or not isinstance(policy, dict) or not isinstance(policy.get('check_scope'), dict)
            or any(type(policy.get(key)) not in (int, float) or not 0 < policy[key] < float('inf')
                   for key in ('max_seconds', 'feedback_seconds'))):
        raise e.IntegrityError('Invalid optional engineering replay declaration')
    stamp = budget.clock_stamp()
    seconds = config.get('max_seconds')
    bound = dict(version=1, capability=dict(key=item.key, schema_id=item.schema_id,
        payload_schema_version=item.payload_schema_version, media_type=item.media_type,
        policy=policy),
        boot_id=stamp['boot_id'], deadline_boottime=(stamp['at_boottime'] + seconds
            if type(seconds) in (int, float) and 0 < seconds < float('inf') else None))
    reserve = reserve_seconds(config)
    if reserve:
        deadline = _call_deadline(config, bound, stamp)
        allocation = dict(requested_seconds=reserve, status='unavailable',
            note='original_window_cannot_fit_check_and_feedback')
        if policy.get('supports_time_reservation') is not True:
            allocation.update(status='unsupported', note='adapter_does_not_partition_execution_window')
        elif (deadline is not None and reserve > policy['feedback_seconds']
                and deadline - stamp['at_boottime'] > reserve):
            bound['deadline_boottime'] = deadline
            allocation.update(status='reserved', note='experiment_and_check_share_original_deadline',
                experiment_deadline_boottime=deadline-reserve,
                check_deadline_boottime=deadline-policy['feedback_seconds'],
                check_seconds=min(policy['max_seconds'], reserve-policy['feedback_seconds']))
        bound['time_allocation'] = allocation
    return bound


def _call_deadline(config, bound, stamp):
    deadline = bound.get('deadline_boottime')
    if (bound.get('boot_id') != stamp['boot_id'] or type(deadline) not in (int, float)
            or not 0 < deadline < float('inf')):
        return None
    mode = config.get('admission', {}).get('mode')
    if mode == 'soft':
        review_status = e.review_status(config)
        if review_status['requested'] and review_status['enforced']:
            return None
    elif mode != 'hard':
        return None
    return deadline


def _deadline(config, bound):
    stamp = budget.clock_stamp()
    deadline = _call_deadline(config, bound, stamp)
    if deadline is None:
        return None
    allocation = bound.get('time_allocation', {})
    if allocation.get('status') == 'unavailable':
        return None
    policy = bound['capability']['policy']
    check_seconds = allocation.get('check_seconds', policy['max_seconds'])
    deadline = min(stamp['at_boottime'] + check_seconds, deadline - policy['feedback_seconds'])
    return deadline if deadline > stamp['at_boottime'] else None


def _preview(directory, accepted, *, scientific=False, config_projection=None):
    bound = accepted.get('engineering_check')
    if bound is None:
        return dict(status='not_run', note='original_call_has_no_bound_engineering_check')
    if bound.get('status') == 'unsupported':
        return bound
    scope = bound['capability']['policy']['check_scope']
    result = dict(status='not_run', note='engineering_check_not_observed', check_scope=scope)
    result['time_allocation'] = bound.get('time_allocation',
        dict(status='not_reserved', requested_seconds=0, note='best_effort_within_original_deadline'))
    result['original_deadline_boottime'] = bound['deadline_boottime']
    if (directory / INTENT).exists():
        result.update(status='unknown', note='accepted_engineering_check_has_no_verified_outcome')
        if (directory / OUTCOME).exists():
            value = json.loads(e.read(directory / OUTCOME))
            intent = json.loads(e.read(directory / INTENT))
            if (any(e.proof(directory/INTENT).get(key)!=item for key,item in value['intent_ref'].items())
                    or any(e.proof(directory/'accepted.json').get(key)!=item for key,item in intent['accepted_ref'].items())
                    or any(e.proof(directory/'completed.json').get(key)!=item for key,item in intent['completed_ref'].items())):
                raise e.IntegrityError('Engineering feedback differs from its original call')
            original = value['result']
            if scientific:
                result.update(status=original['status'],note='isolated_replay_'+original['status'],
                    cleanup_confirmed=original['cleanup_confirmed'])
                if config_projection is not None:
                    result.update(config_projection(original))
            else:
                result.update({key: original[key] for key in ('status', 'note', 'samples', 'stage', 'cleanup_confirmed', 'completion') if key in original})
            if 'diagnostic' in original:
                if scientific:
                    result['diagnostic']=dict(present=True,code='isolated_replay_diagnostic',
                        limitation='The isolated sample did not establish compatibility; inspect the scientific implementation and declared dependencies. Exact process diagnostics remain in private originals.')
                else:
                    raw = str(original['diagnostic']).encode()
                    result['diagnostic'] = raw[:1024].decode(errors='ignore')
                    result['diagnostic_omitted_bytes'] = original.get('diagnostic_omitted_bytes', 0) + max(0, len(raw) - 1024)
    return result


def public(config, call_id):
    """A bounded read-only view; query never starts a replay."""
    try:
        records = e.Records(config)
        declared,_=e.registration(config['execution_interface']['executor'])
        return _preview(records.directory(call_id),records.accepted(call_id),
            scientific=config.get('worker_view_version')==1,config_projection=declared.engineering_feedback)
    except (OSError, ValueError, KeyError, TypeError):
        return dict(status='unknown', note='engineering_feedback_original_unavailable')


@e._configuration_effect
def complete(config, adapter, call_id, observation):
    """One optional call after confirmed terminal evidence; never repeat an intent.

    All failures here remain engineering feedback. They cannot erase the accepted
    experiment, change its result or change the successful experiment's CLI code.
    """
    if observation is None:
        return
    records = e.Records(config)
    directory = records.directory(call_id)
    try:
        with records.lease('engineering-' + call_id + '.lock'):
            accepted = records.accepted(call_id)
            bound = accepted.get('engineering_check')
            if bound is None or bound.get('status') == 'unsupported' or (directory / INTENT).exists():
                return
            # record_observation already verified the complete backend originals.
            # Reuse that fact; only the small accepted/completed bindings need
            # rechecking here, not every model/log/public data file again.
            if (any(e.proof(directory/'accepted.json').get(key)!=item for key,item in observation['accepted'].items())
                    or any(e.proof(directory/'completed.json').get(key)!=item for key,item in observation['completed'].items())):
                raise e.IntegrityError('Engineering check has no exact confirmed completion')
            declared = [item for item in adapter.capabilities() if item.key == KEY]
            available = (len(declared) == 1 and callable(getattr(adapter,'replay_check',None)))
            deadline = _deadline(config, bound)
            intent = dict(accepted_ref=e.proof(directory / 'accepted.json'),
                completed_ref=e.proof(directory / 'completed.json'),
                check=bound, deadline_boottime=deadline)
            e.write(directory / INTENT, intent)
            if not available:
                result = dict(status='not_run', note='original_engineering_capability_unavailable', cleanup_confirmed=True)
            elif observation['status'] != 'known_success':
                result = dict(status='not_run', note='original_execution_not_successful', cleanup_confirmed=True)
            elif deadline is None or deadline <= time.clock_gettime(time.CLOCK_BOOTTIME):
                result = dict(status='not_run', note='original_budget_or_feedback_margin_unavailable', cleanup_confirmed=True)
            else:
                def process(phase, identity):
                    e.write(directory / 'engineering-check.process.json', dict(phase=phase, identity=identity))
                try:
                    result = adapter.replay_check(call_id, deadline_boottime=deadline, on_process=process)
                except Exception as error:
                    result = dict(status='unknown', note='engineering_check_unverified', cleanup_confirmed=False,
                        diagnostic=type(error).__name__ + ': ' + str(error))
                if (not isinstance(result, dict) or result.get('status') not in STATES
                        or type(result.get('cleanup_confirmed')) is not bool
                        or not isinstance(result.get('note'), str)):
                    result = dict(status='unknown', note='invalid_engineering_check_result', cleanup_confirmed=False)
                if not result['cleanup_confirmed']:
                    result = dict(result, status='unknown')
            e.write(directory / OUTCOME, dict(intent_ref=e.proof(directory / INTENT), result=result))
    except Exception:
        return  # The original scientific result and every already-written receipt remain intact.


def cleanup_confirmed(config, call_id, observation):
    """Read existing cleanup evidence without starting or repeating a replay."""
    try:
        records=e.Records(config);directory=records.directory(call_id)
        accepted=records.accepted(call_id);bound=accepted.get('engineering_check')
        if (observation['call_id']!=call_id
                or observation['accepted']!=e.proof(directory/'accepted.json')
                or observation['completed']!=e.proof(directory/'completed.json')):
            return False
        intent_path=directory/INTENT;outcome_path=directory/OUTCOME
        if not intent_path.exists() and not intent_path.is_symlink():
            return ((bound is None or bound.get('status')=='unsupported')
                and not outcome_path.exists() and not outcome_path.is_symlink())
        intent=json.loads(e.read(intent_path));outcome=json.loads(e.read(outcome_path))
        result=outcome['result']
        return (intent['accepted_ref']==observation['accepted']
            and intent['completed_ref']==observation['completed'] and intent['check']==bound
            and outcome['intent_ref']==e.proof(intent_path)
            and result.get('status') in STATES and isinstance(result.get('note'),str)
            and result.get('cleanup_confirmed') is True)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
