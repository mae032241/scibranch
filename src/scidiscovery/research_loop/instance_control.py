"""Shared CLI/UI instance controls; the original controller owns all advancement."""
from pathlib import Path

from . import budget, contracts, controller, native, runtime, correction, execution, feedback, history
from .instance_owner import effect as owner_effect


def status(root: Path, *, after=0, limit=None, live_feedback=True):
    root = root.resolve()
    settings = contracts.settings(root)
    from . import protocol
    protocol.require_settings_format(settings)
    if type(after) is not int or after < 0 or (limit is not None and (type(limit) is not int or limit <= 0)):
        raise ValueError('Exact positive node page and continuation required')
    nodes = []
    from . import fork_runtime, originals
    local = fork_runtime.local_steps(root)
    inherited = {}
    if fork_runtime.is_fork(root):
        from . import fork_sources
        inherited = {row['step']: row for row in fork_sources.validate_manifest(root)['prefix_entries']}
    numbers = sorted(set(local) | set(inherited))
    selected = [step for step in numbers if step > after]
    if limit is not None:
        selected = selected[:limit]
    for step in selected:
        job_path = root / 'jobs' / f'{step:03d}.json'
        if step not in local:
            job = originals.source_job(originals.node(root, step))
            protocol.require_job_format(job)
            nodes.append(dict(step=step, role=job['role'], state='seal_present_unverified', inherited=True))
            continue
        job = contracts.read(job_path)
        protocol.require_job_format(job)
        record=execution.record(root,step)
        outcome=(contracts.native_outcome(record) if (record/'native-outcome.json').exists() else None)
        if outcome is not None and not isinstance(outcome,dict):
            raise history.ObserverIntegrityError('Native status original outcome is malformed')
        state = runtime.node_state(root, step)
        state = {'sealed': 'seal_present_unverified', 'terminal': 'native_outcome_unsealed',
                 'unstarted': 'prepared'}.get(state, state)
        if state == 'native_outcome_unsealed':
            if outcome is not None and outcome.get('native_started') is False:
                state = 'native_not_submitted'
        node = dict(step=step, role=job['role'], state=state)
        if not live_feedback:
            if outcome is not None:
                node.update(stop_reason=outcome.get('stop_reason'), returncode=outcome.get('returncode'))
        config_path=record/'experiment-config.json'
        if config_path.exists() and 'experiment_mode' in job:
            selected=contracts.read(contracts.regular(config_path))['execution_interface']
            node['execution']=dict(executor=selected['executor'],capabilities=selected['capabilities'],
                result_protocol=selected['result_protocol'])
        if settings.get('node_budget_policy') is not None:
            intent = contracts.read(record / 'native-intent.json') if (record / 'native-intent.json').exists() else {}
            if not isinstance(intent, dict):
                raise history.ObserverIntegrityError('Native budget status originals are malformed')
            node.update(**(dict(node_budget_mode='soft') if budget.node_mode(root)=='soft' else {}), budget_slice=intent.get('budget_slice'), budget_report=(outcome or {}).get('budget_slice'), stop_reason=(outcome or {}).get('stop_reason'))
        if settings.get('resume_policy') is not None or record != execution.origin(root,step):
            node['continuations'] = len(execution.records(root, step)) - 1
        if outcome is not None and state != 'seal_present_unverified':
            from . import continuation
            stopped=continuation.technical_stop(root,job,record,outcome)
            if stopped is not None:node.update(technical_stop=stopped,stage=stopped['stage'])
        if live_feedback and job.get('live_feedback_version') == 1 and state != 'seal_present_unverified':
            node['live_feedback'] = feedback.live(root, job)
        nodes.append(node)
    page = {}
    if limit is not None:
        last = nodes[-1]['step'] if nodes else after
        more = any(step > last for step in numbers)
        page = dict(after=after, next_after=last if more else None)
    closeout = budget.closeout_state(root)
    from . import delivery
    technical=(nodes[-1].get('technical_stop') if nodes else None)
    return dict(instance=str(root), goal_id=settings['goal_id'], objective=settings['objective'],
                **(dict(technical_stop=technical,stage=technical['stage'],
                    pending_step=technical['pending_step'],pending_role=technical['pending_role'],
                    repair_scope=technical['repair_scope'],supported_actions=technical['supported_actions']) if technical else {}),
                paused=(root / 'pause.request').exists(), nodes=nodes, usage=native.usage(root),
                goal_completion='not_asserted', budget=budget.status_snapshot(root),
                correction=correction.project(root, before_step=closeout['begin']['source_cutoff'] + 1 if closeout['begin'] else None),
                closeout_preparation=delivery.preparation_status(root),
                closeout=controller._closeout_result(root, 'closeout_status', read_only=True) if closeout['begin'] else None,
                used_steps=budget.used_steps(root),
                **(dict(fork=dict(inherited_costs=fork_sources.validate_manifest(root).get('inherited_costs',
                    dict(status='unknown', reason='Exact cut costs are not reconstructed')))) if inherited or fork_runtime.is_fork(root) else {}), **page)


@owner_effect
def request_pause(root):
    root = Path(root).resolve()
    from .protocol import require_current
    require_current(root)
    contracts.read(root / 'settings.json')
    contracts.write(root / 'pause.request', dict(reason='user_pause_at_next_node'))
    try:
        with controller._writer(root):
            budget.inactive(root, 'operator_pause')
    except RuntimeError:
        pass  # Accepted work retains advance time; its owner will pause.
    return dict(status='pause_requested', accepted_work='allowed_to_finish_and_seal')


def continue_instance(root, *, ack_correction=None, authorization_note=None):
    root = Path(root).resolve()
    # A second caller cannot clear a live owner's next-node pause.
    with controller._writer(root), controller._inactive_on_error(root):
        if ack_correction is not None:
            correction.acknowledge(root, ack_correction, authorization_note)
        elif authorization_note is not None:
            raise ValueError('An authorization note requires an exact correction trigger')
        target = controller._wait_continuation_target(root)
        (root / 'pause.request').unlink(missing_ok=True)
        result = controller._run(root, continue_wait=target)
        from . import control_operations
        control_operations.apply_pending_budget(root)
        return result


# CLI and thin UI callers use the same durable control operations.
from .control_operations import start_background, request_budget_amendment, operations, operation
