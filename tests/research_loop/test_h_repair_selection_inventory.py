"""H revision repairs consume current accepted selection and saved replay facts."""
import copy

import pytest

from scidiscovery.research_loop import contracts as c, controller, execution, experiments, native, runtime, search
from execution_fixtures import execution_registry
from breadth_execution_fixtures import run as qualified_run
from test_breadth_science import findings, prepare, worker
from test_direct_public_collect import stopped_attempt
from test_history_handoff import finish
from test_search import focus, plan


pytestmark = pytest.mark.process_e2e


@pytest.fixture
def repair_scene(tmp_path, monkeypatch, execution_registry):
    root = prepare(tmp_path, qualified_local=True, initial_calls=2)
    original = c.seal_result

    def seal_stopped(root, step, *, inventory=None):
        if inventory is None and not (execution.origin(root, step) / 'result-identity.json').exists():
            with monkeypatch.context() as stopped:
                stopped_attempt(root, c.read(root / 'jobs' / f'{step:03d}.json'),
                    execution.record(root, step), stopped)
                return original(root, step, inventory=inventory)
        return original(root, step, inventory=inventory)

    monkeypatch.setattr(c, 'seal_result', seal_stopped)
    finish(root, 1, 'H', [], action='experiment', search_choice=plan())
    _, config = worker(root, tmp_path, 2, [1], qualified_local=True)
    return root, config


def retained_failed_call(root, config, name):
    directory = c.workspace(root, 2) / 'code'
    directory.mkdir(exist_ok=True)
    source = directory / name
    source.write_text('raise SystemExit(3)\n')
    runtime = c.workspace(root, 2) / 'experiments/repair-fixture'
    runtime.mkdir(parents=True, exist_ok=True)
    result = qualified_run(root, config, source, 'h001.m1', runtime)
    assert result['status'] == 'known_failed'
    return result


def revise(root):
    finish(root, 2, 'W', [1], mechanism_findings=findings('h001.m1'),
        preserve_native_intent=True)
    finish(root, 3, 'A', [1, 2], action='revise_h')


def repair_plan():
    return plan('Revised discriminating measurement', 'h001.m1', 'repair')


def control(root, step):
    return c.read(execution.origin(root, step) / 'result-identity.json')['control_outcome']


def test_public_nonrepair_interpret_does_not_accept_material_selection(tmp_path, monkeypatch):
    state = dict(mechanisms={}, current_h=None, scope=None, plan=None, authorized=set(), focus=None,
        order=dict(remaining=[], seen_w=set(), allocation=None, diagnostics=[], discriminating_work=[]))
    monkeypatch.setattr(c, 'settings', lambda *args: dict(search_policy=dict(search.DEFAULT_POLICY)))

    def forbidden(*args, **kwargs):
        raise AssertionError('A nonrepair interpretation acquired material selection')

    monkeypatch.setattr(c, '_accept_selected_inputs', forbidden)
    result = search.interpret(tmp_path, dict(role='H', step=1),
        dict(next_action='experiment', route_reason='Exact scientific question', search_plan=plan()), state=state)
    assert result['diagnostics'] == []
    assert result['focus'] == focus(action='screen')


def test_h_revision_seals_repair_and_replays_without_rerunning(repair_scene, monkeypatch):
    root, config = repair_scene
    call = retained_failed_call(root, config, 'failed-input.json')
    revise(root)
    publish = c._seal_result

    def interrupted(*args, **kwargs):
        raise RuntimeError('Fixture interrupted publication after current inventory acceptance')

    monkeypatch.setattr(c, '_seal_result', interrupted)
    with pytest.raises(RuntimeError, match='interrupted publication'):
        finish(root, 4, 'H', [1, 2, 3], action='experiment', search_choice=repair_plan())
    record = execution.origin(root, 4)
    assert not (record / 'result-identity.json').exists()
    originals = {path: path.read_bytes() for path in (
        root / 'jobs/004.json', record / 'science-output.json',
        experiments.Records(config).directory(call['call_id']) / 'accepted.json')}

    def forbidden(*args, **kwargs):
        raise AssertionError('Replay reaccepted scientific selection or repeated execution')

    monkeypatch.setattr(c, '_seal_result', publish)
    monkeypatch.setattr(native, 'execute_native', forbidden)
    monkeypatch.setattr(experiments, 'dispatch', forbidden)
    (root / 'pause.request').write_text('Collect the completed H original before any new native work.')

    def stopped_process(receipt, expected):
        assert receipt['task_ref'] == expected
        return False

    with monkeypatch.context() as recovering:
        recovering.setattr(runtime, 'node_state', lambda *args: 'terminal')
        recovering.setattr(runtime, 'process_matches', stopped_process)
        recovered = controller.run(root)
        assert recovered['status'] == 'paused', recovered
        assert recovered['completed_steps'] == 4
        assert controller.run(root)['status'] == 'paused'
    assert all(path.read_bytes() == body for path, body in originals.items())
    originals[record / 'result-identity.json'] = (record / 'result-identity.json').read_bytes()
    assert control(root, 4)['search_status'] == 'applied'
    assert control(root, 4)['resolved_focus'] == focus(action='repair')

    monkeypatch.setattr(c, '_accept_selected_inputs', forbidden)
    assert c.seal_result(root, 4) == c.seal_result(root, 4)
    job = c.build_job(root, 5, 'W', [4])
    bound = job['search']
    assert search.launch_reason(root, job) is None
    assert bound['repair_binding'] == 'available'
    assert bound['repair_root'] == 2
    assert bound['repair_source']['step'] == 2
    assert bound['repair_source']['call']['call_id'] == call['call_id']
    assert bound['repair_source']['call']['mechanism_binding']['source_h'] == 1
    assert bound['repair_authorization']['step'] == 4
    assert bound['breadth']['mechanisms'][0]['source_h'] == 4
    assert bound['breadth']['baseline'] == [dict(mechanism_ref='h001.m1', accepted_count=1)]
    assert search.project(root, 5)['repairs'] == {}
    assert search.project(root, 5)['opportunity_counts'] == {'h001.m1': 1}
    assert [path.name for path in experiments.Records(config).calls()] == [call['call_id']]
    assert all(path.read_bytes() == body for path, body in originals.items())


def test_h_repair_keeps_multiple_original_calls_ambiguous(repair_scene):
    root, config = repair_scene
    retained_failed_call(root, config, 'first-input.json')
    retained_failed_call(root, config, 'second-input.json')
    revise(root)
    _, _, record = finish(root, 4, 'H', [1, 2, 3], action='experiment', search_choice=repair_plan())
    receipt = control(root, 4)
    assert receipt['selection_status'] == 'applied'
    assert receipt['search_status'] == 'unresolved'
    assert dict(code='search_repair_source_ambiguous', subject='h001.m1') in receipt['diagnostics']
    assert not c.read(record / 'result-identity.json').get('selected_inputs')
    assert search.project(root, 5)['opportunity_counts'] == {'h001.m1': 2}


def test_h_repair_consumes_current_accepted_selection_and_saved_replay(repair_scene, monkeypatch):
    root, config = repair_scene
    call = retained_failed_call(root, config, 'failed-input.json')
    material = 'inputs/parents/002/artifacts/implementation.json'
    (c.workspace(root, 2) / 'artifacts/implementation.json').write_text('{"fixture":"retained implementation"}')
    revise(root)
    seal = c.read(execution.origin(root, 2) / 'result-identity.json')
    number = next(index for index, row in enumerate(sorted(seal['materials'], key=lambda row: row['path']), 1)
        if row['path'] == material)
    selected = [f'W002-I{number:03d}']
    accepted = []
    accept_selection = c._accept_selected_inputs
    received = []
    repair_subject = search._repair_subject

    def record_selection(*args, **kwargs):
        result = accept_selection(*args, **kwargs)
        accepted.append(result)
        return result

    def record_subject(root, job, ref, *, selected_inputs):
        received.append(selected_inputs)
        return repair_subject(root, job, ref, selected_inputs=selected_inputs)

    monkeypatch.setattr(c, '_accept_selected_inputs', record_selection)
    monkeypatch.setattr(search, '_repair_subject', record_subject)
    _, _, record = finish(root, 4, 'H', [1, 2, 3], action='experiment',
        search_choice=repair_plan(), selection=selected)
    assert len(accepted) == 1
    assert received == accepted
    assert received[0] is accepted[0]
    assert received[0][0]['material']['path'] == material
    receipt = control(root, 4)
    assert receipt['selection_status'] == 'applied'
    assert receipt['search_status'] == 'applied'

    saved = copy.deepcopy(c.read(record / 'result-identity.json')['selected_inputs'])
    assert saved[0]['material_ref']['id'] == selected[0]
    assert 'material' not in saved[0]

    def forbidden(*args, **kwargs):
        raise AssertionError('Historical binding reaccepted the chooser payload')

    monkeypatch.setattr(c, '_accept_selected_inputs', forbidden)
    job = c.build_job(root, 5, 'W', [4])
    assert job['search']['repair_binding'] == 'available'
    assert job['search']['repair_source']['call']['call_id'] == call['call_id']
    assert received[-1][0]['material']['path'] == material
    assert job['search']['breadth']['baseline'] == [dict(mechanism_ref='h001.m1', accepted_count=1)]
    assert job['selected_inputs'][0]['material']['path'] == material
    assert c.read(record / 'result-identity.json')['selected_inputs'] == saved
    assert search.project(root, 5)['opportunity_counts'] == {'h001.m1': 1}
