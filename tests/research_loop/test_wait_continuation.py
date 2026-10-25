"""Explicit continuation consumes one exact sealed A wait, without providers."""
import fcntl
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import budget, contracts, controller, control_operations, instance_control, native, role_share
from test_history_handoff import finish, scene


@pytest.fixture(autouse=True)
def _stopped_source_seals(request, monkeypatch):
    if request.node.get_closest_marker('process_e2e') is None:
        from test_direct_input_bindings import stopped_seals
        stopped_seals(monkeypatch)


def fixture_launch(monkeypatch, actions):
    calls = []

    def execute(root, step):
        calls.append(step)
        job = contracts.read(root / 'jobs' / f'{step:03d}.json')
        parents = [int(Path(ref['path']).parts[2]) for ref in job['direct_refs']]
        finish(root, step, job['role'], parents, action=actions[step])

    monkeypatch.setattr(native, 'execute_native', execute)
    return calls


def test_run_keeps_wait_and_continue_retains_originals_and_shared_usage(scene, monkeypatch):
    calls = fixture_launch(monkeypatch, {1: 'analyze', 2: 'wait', 3: 'wait', 4: 'finish'})
    assert controller.run(scene)['status'] == 'wait'
    originals = {path: path.read_bytes() for directory in ('jobs', 'native-evidence')
                 for path in (scene / directory).rglob('*') if path.is_file()}
    settings = (scene / 'settings.json').read_bytes()
    started = (scene / 'host-started.json').read_bytes()
    usage = native.usage(scene)['accounted_tokens']
    assert controller.run(scene)['status'] == 'wait'
    assert calls == [1, 2]

    result = instance_control.continue_instance(scene)
    assert result['status'] == 'wait' and result['last_step'] == 3
    assert calls == [1, 2, 3]
    job = contracts.read(scene / 'jobs/003.json')
    assert job['role'] == 'A'
    assert [ref['path'] for ref in job['direct_refs']] == [
        'inputs/parents/001/conclusion.json', 'inputs/parents/002/conclusion.json']
    transition = contracts.read(scene / 'host-control/wait-continuations/002.json')
    assert transition == dict(source_step=2,
        seal_ref=contracts.record_ref(scene / 'native-evidence/002/result-identity.json'))
    assert all(path.read_bytes() == original for path, original in originals.items())
    assert (scene / 'settings.json').read_bytes() == settings
    assert (scene / 'host-started.json').read_bytes() == started
    assert native.usage(scene)['accounted_tokens'] == usage + 10
    assert controller.run(scene)['last_step'] == 3
    assert calls == [1, 2, 3]
    assert instance_control.continue_instance(scene)['status'] == 'finish'
    assert calls == [1, 2, 3, 4]


def test_detached_continue_binds_wait_and_receipt_replay_cannot_continue_next_wait(scene, monkeypatch):
    calls = fixture_launch(monkeypatch, {1: 'analyze', 2: 'wait', 3: 'wait'})
    assert controller.run(scene)['status'] == 'wait'
    monkeypatch.setattr(control_operations.subprocess, 'Popen',
        lambda *args, **kwargs: SimpleNamespace(pid=os.getpid()))
    ordinary = control_operations.start_background(scene)
    assert ordinary['action'] == dict(kind='run')
    receipt = control_operations.start_background(scene, continue_run=True)
    target = receipt['action']['wait_target']
    assert target['source_step'] == 2

    def child(operation_id):
        descriptor = os.open(scene / 'host-run.lock', os.O_RDWR)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        control_operations._child(scene, operation_id, descriptor)

    child(ordinary['operation_id'])
    assert calls == [1, 2]
    child(receipt['operation_id'])
    final = control_operations._read(scene, receipt['operation_id'])
    assert final['status'] == 'applied' and final['result']['last_step'] == 3
    child(receipt['operation_id'])
    assert control_operations._read(scene, receipt['operation_id']) == final
    # Reconcile the same operation after a crash before its final receipt write.
    contracts.write(control_operations._path(scene, receipt['operation_id']),
        dict(final, status='running'))
    child(receipt['operation_id'])
    assert calls == [1, 2, 3]
    assert not (scene / 'jobs/004.json').exists()
    assert not (scene / 'host-control/wait-continuations/003.json').exists()


@pytest.mark.parametrize('unknown', ['native', 'execution'])
def test_unknown_work_cannot_create_a_wait_transition(scene, monkeypatch, unknown):
    calls = fixture_launch(monkeypatch, {1: 'analyze', 2: 'wait'})
    assert controller.run(scene)['status'] == 'wait'
    if unknown == 'native':
        original_usage = native.usage
        monkeypatch.setattr(native, 'usage', lambda root: dict(original_usage(root),
            blocking_unknown_steps=[2], unknown_steps=[2]))
    else:
        monkeypatch.setattr(budget, 'execution_blocked', lambda root, **kwargs: True)
    assert instance_control.continue_instance(scene)['status'] == 'wait'
    assert calls == [1, 2]
    assert not (scene / 'host-control/wait-continuations/002.json').exists()
    assert not (scene / 'jobs/003.json').exists()


def test_continue_cannot_clear_an_existing_writers_pause(scene, monkeypatch):
    calls = fixture_launch(monkeypatch, {1: 'analyze', 2: 'wait'})
    assert controller.run(scene)['status'] == 'wait'
    (scene / 'pause.request').touch()
    with controller._writer(scene):
        with pytest.raises(RuntimeError, match='running controller'):
            instance_control.continue_instance(scene)
        result = control_operations.start_background(scene, continue_run=True)
        assert result['already_running']
    assert (scene / 'pause.request').exists()
    assert calls == [1, 2]
    assert not (scene / 'host-control/wait-continuations/002.json').exists()


def test_continuation_keeps_the_existing_final_budget_boundary(scene, monkeypatch):
    calls = fixture_launch(monkeypatch, {1: 'analyze', 2: 'wait'})
    assert controller.run(scene)['status'] == 'wait'
    budget.amend(scene, max_steps=2, authorization_note='Retain exactly two consumed exploration steps',
        authorization_statement='Keep the exploration ceiling at the two steps already consumed.')
    final = []

    def final_boundary(root, begin):
        final.append(begin)
        return dict(status='final_a_boundary')

    monkeypatch.setattr(controller, '_run_final', final_boundary)
    assert instance_control.continue_instance(scene)['status'] == 'final_a_boundary'
    assert calls == [1, 2]
    begin = budget.closeout_state(scene)['begin']
    assert begin['mode'] == 'final' and begin['source_cutoff'] == 2
    assert final == [begin]
    assert native.usage(scene)['accounted_tokens'] == 20
    assert budget.snapshot(scene)['max_steps'] == 2
    assert not (scene / 'jobs/003.json').exists()


def test_h_wait_without_pending_is_final_not_an_author_continuation(scene, monkeypatch):
    calls = fixture_launch(monkeypatch, {1: 'wait'})
    monkeypatch.setattr(controller, '_run_final', lambda root, begin: dict(status='final_boundary'))
    assert controller.run(scene)['status'] == 'final_boundary'
    assert instance_control.continue_instance(scene)['status'] == 'final_boundary'
    assert budget.closeout_state(scene)['begin']['stop_reason'] == 'scientific_wait'
    assert calls == [1]
    assert not (scene / 'host-control/wait-continuations').exists()


def role_policy_scene(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain the original role allocation during explicit continuation.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'],
        max_tokens=10_000_000, max_seconds=600, max_steps=12, model='fixture',
        reasoning_effort='high', search_policy=None,
        node_budget_policy={role: dict(max_observed_tokens=500_000, max_seconds=60) for role in 'HWAE'},
        role_budget_policy=role_share.DEFAULT)
    return root


@pytest.mark.parametrize('limit', ['thinking', 'w_floor'])
def test_explicit_continue_preserves_aggregate_role_limits(tmp_path, monkeypatch, limit):
    from test_role_budget_share import drive
    root = role_policy_scene(tmp_path)
    if limit == 'thinking':
        script = {1: dict(action='experiment', tokens=100_000),
                  2: dict(tokens=500_000), 3: dict(action='wait', tokens=3_400_000)}
        expected = 'role_share_thinking_reached'
    else:
        script = {1: dict(action='extract', tokens=100_000), 2: dict(tokens=3_300_000),
                  3: dict(action='experiment', tokens=100_000),
                  4: dict(tokens=500_000), 5: dict(action='wait', tokens=100_000)}
        expected = 'role_share_w_floor_reached'
    last = max(script)
    script.update({last + 1: dict(action='experiment', tokens=10),
        last + 2: dict(tokens=10), last + 3: dict(action='finish', tokens=10)})
    launched = drive(monkeypatch, script)
    assert controller.run(root)['status'] == 'wait'
    before = list(launched)
    assert instance_control.continue_instance(root)['status'] == 'finish'
    assert launched == [*before, (last + 1, 'H'), (last + 2, 'W'), (last + 3, 'A')]
    job = contracts.read(root / 'jobs' / f'{last + 1:03d}.json')
    request = job['author_correction']
    assert request['reason'] == expected and request['root']['step'] == last
    assert contracts.read(root / 'host-control/wait-continuations' / f'{last:03d}.json') == dict(
        source_step=last, seal_ref=contracts.record_ref(root / 'native-evidence' / f'{last:03d}' / 'result-identity.json'))
    assert budget.closeout_state(root)['begin'] is None
    assert contracts.read(root / 'jobs' / f'{last + 2:03d}.json').get('author_correction') is None


@pytest.mark.parametrize('marker', ['missing', 'wrong'])
def test_wait_admission_replan_requires_exact_explicit_continuation(tmp_path, monkeypatch, marker):
    from test_role_budget_share import drive
    from scidiscovery.research_loop import semantic
    root = role_policy_scene(tmp_path)
    launched = drive(monkeypatch, {1: dict(action='analyze', tokens=100_000),
        2: dict(action='wait', tokens=3_400_000)})
    assert controller.run(root)['status'] == 'wait'
    job = contracts.read(root / 'jobs/002.json')
    result = contracts.validate(contracts.workspace(root, 2))
    route = ('A', [1, 2], 'Explicit continuation', None, 1, None, None, None)
    code = role_share.reason(root, 2, job, result, 'A', allow_repeat=True)
    assert code == 'role_share_thinking_reached'
    corrected = role_share.reconsider(root, 2, job, route, code)
    if marker == 'wrong':
        contracts.write(root / 'host-control/wait-continuations/002.json', dict(source_step=1,
            seal_ref=contracts.record_ref(root / 'native-evidence/002/result-identity.json')))
    candidate = contracts.build_job(root, 3, 'H', corrected[1], instruction=corrected[2], author_correction=corrected[7])
    with pytest.raises(ValueError, match='exact explicit wait continuation'):
        semantic.validate_job(root, candidate)
    assert launched == [(1, 'H'), (2, 'A')]


def test_explicit_continue_only_waives_repeat_without_evidence(tmp_path, monkeypatch):
    from test_role_budget_share import drive
    root = role_policy_scene(tmp_path)
    launched = drive(monkeypatch, {1: dict(action='analyze', tokens=100_000),
        2: dict(action='wait', tokens=100_000), 3: dict(action='finish', tokens=100_000)})
    assert controller.run(root)['status'] == 'wait'
    job = contracts.read(root / 'jobs/002.json')
    result = contracts.validate(contracts.workspace(root, 2))
    assert role_share.reason(root, 2, job, result, 'A') == 'role_repeat_without_evidence'
    assert role_share.reason(root, 2, job, result, 'A', allow_repeat=True) is None
    assert instance_control.continue_instance(root)['status'] == 'finish'
    assert launched == [(1, 'H'), (2, 'A'), (3, 'A')]
    assert role_share.usage(root, 3)['thinking'] == 300_000
