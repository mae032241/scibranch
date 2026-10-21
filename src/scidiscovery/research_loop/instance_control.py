"""Shared CLI/UI instance controls; the original controller owns all advancement."""
from pathlib import Path

from . import budget, contracts, controller, native, runtime, correction, execution, feedback, history
from .instance_owner import effect as owner_effect


def status(root: Path, *, after=0, limit=None, live_feedback=True):
    root = root.resolve()
    settings = contracts.settings(root)
    from . import protocol
    if type(settings.get('handoff_protocol')) is not int or settings['handoff_protocol']!=protocol.CURRENT:
        return protocol.archive_status(root)
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
            nodes.append(dict(step=step, role=job['role'], state='seal_present_unverified', inherited=True))
            continue
        job = contracts.read(job_path)
        state = runtime.node_state(root, step)
        state = {'sealed': 'seal_present_unverified', 'terminal': 'native_outcome_unsealed',
                 'unstarted': 'prepared'}.get(state, state)
        if state == 'native_outcome_unsealed':
            outcome = contracts.read(execution.record(root, step) / 'native-outcome.json')
            if not isinstance(outcome, dict):
                raise history.ObserverIntegrityError('Native status original outcome is malformed')
            if outcome.get('native_started') is False:
                state = 'native_not_submitted'
        node = dict(step=step, role=job['role'], state=state)
        if not live_feedback:
            outcome_path = execution.record(root, step) / 'native-outcome.json'
            if outcome_path.exists():
                outcome = contracts.read(contracts.regular(outcome_path))
                if not isinstance(outcome, dict):
                    raise history.ObserverIntegrityError('Native status original outcome is malformed')
                node.update(stop_reason=outcome.get('stop_reason'), returncode=outcome.get('returncode'))
        config_path=execution.record(root,step)/'experiment-config.json'
        if config_path.exists() and 'experiment_mode' in job:
            selected=contracts.read(contracts.regular(config_path))['execution_interface']
            node['execution']=dict(executor=selected['executor'],capabilities=selected['capabilities'],
                result_protocol=selected['result_protocol'])
        if settings.get('node_budget_policy') is not None:
            record = execution.record(root, step)
            intent = contracts.read(record / 'native-intent.json') if (record / 'native-intent.json').exists() else {}
            outcome = contracts.read(record / 'native-outcome.json') if (record / 'native-outcome.json').exists() else {}
            if not isinstance(intent, dict) or not isinstance(outcome, dict):
                raise history.ObserverIntegrityError('Native budget status originals are malformed')
            node.update(**(dict(node_budget_mode='soft') if budget.node_mode(root)=='soft' else {}), budget_slice=intent.get('budget_slice'), budget_report=outcome.get('budget_slice'), stop_reason=outcome.get('stop_reason'))
        if settings.get('resume_policy') is not None:
            node['continuations'] = len(execution.records(root, step)) - 1
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
    return dict(instance=str(root), goal_id=settings['goal_id'], objective=settings['objective'],
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
