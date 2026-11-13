"""Scientific routing and recovery controls, without native model execution."""
from __future__ import annotations

import importlib
import json
import multiprocessing
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def scene(tmp_path, monkeypatch):
    """A current worker-first instance whose native stand-in seals real nodes."""
    from scidiscovery.research_loop import controller, contracts, native
    from test_history_handoff import finish
    root = tmp_path / "research"
    objective = tmp_path / "objective.txt"
    objective.write_text("Measure the unresolved mechanism")
    public = tmp_path / "public"
    public.mkdir()
    controller.prepare(root, objective, public, ["fake-launcher"], max_tokens=1000,
        max_seconds=60, max_steps=20, model="fake-model", reasoning_effort="high", search_policy=None)
    calls = []
    pause_after = set()

    def execute_native(root, step):
        calls.append(step)
        job = contracts.read(root / "jobs" / f"{step:03d}.json")
        finish(root, step, job["role"], parents_of(job))
        if step in pause_after:
            (root / "pause.request").write_text("Pause before further work")

    native_stub = SimpleNamespace(execute_native=execute_native, usage=native.usage)
    monkeypatch.setattr(controller, "native", native_stub)
    return SimpleNamespace(controller=controller, contracts=contracts, root=root, calls=calls,
        pause_after=pause_after, native=native_stub, read=contracts.read, write=contracts.write)


def parents_of(job):
    return [int(Path(ref["path"]).parent.name) for ref in job["direct_refs"]]


def job(scene, step):
    return scene.read(scene.root / "jobs" / f"{step:03d}.json")


def record(scene, step):
    return scene.contracts.host_record(scene.contracts.workspace(scene.root, step))


def test_first_step_is_worker_and_paused_restart_reuses_only_sealed_nodes(scene):
    scene.pause_after.add(1)
    assert scene.controller.run(scene.root)["status"] == "paused"
    assert job(scene, 1)["role"] == "W"
    start = scene.read(scene.root / "host-started.json")
    (scene.root / "pause.request").unlink()
    scene.pause_after.add(2)
    assert scene.controller.run(scene.root)["status"] == "paused"
    assert scene.calls == [1, 2]
    assert parents_of(job(scene, 2)) == [1]
    assert scene.read(scene.root / "host-started.json") == start
    assert scene.controller.run(scene.root)["status"] == "paused"
    assert scene.calls == [1, 2]


@pytest.mark.parametrize("known_outcome", [False, True])
def test_unsealed_original_is_never_replayed(scene, known_outcome):
    origin = record(scene, 1)
    scene.write(origin / "native-intent.json", {"step": 1})
    if known_outcome:
        scene.write(origin / "native-outcome.json", dict(returncode=0, stop_reason=None,
            usage=dict(unknown_steps=[])))
    assert scene.controller.run(scene.root)["status"] == "native_outcome_unsealed"
    assert scene.calls == []


def test_native_failure_stops_without_a_replacement(scene):
    from scidiscovery.research_loop import accepted_budget

    def crash(root, step):
        scene.calls.append(step)
        origin = record(scene, step)
        intent = dict(task_ref=scene.contracts.record_ref(root / "jobs" / f"{step:03d}.json"))
        scene.write(origin / "native-intent.json", intent)
        accepted_budget.accept_native(root, step, origin, intent)
        raise RuntimeError("Submission acceptance was lost")

    scene.native.execute_native = crash
    assert scene.controller.run(scene.root)["status"] == "native_stopped"
    assert scene.controller.run(scene.root)["status"] == "native_outcome_unsealed"
    assert scene.calls == [1]


def test_replaced_seal_marker_cannot_route(scene):
    scene.pause_after.add(1)
    assert scene.controller.run(scene.root)["status"] == "paused"
    (scene.root / "pause.request").unlink()
    output = record(scene, 1) / "result-identity.json"
    replacement = output.with_name("replacement.json")
    replacement.write_bytes(output.read_bytes())
    output.unlink()
    output.symlink_to(replacement)
    with pytest.raises(ValueError, match="no regular acceptance marker"):
        scene.controller.run(scene.root)
    assert scene.calls == [1]


def test_pause_before_launch_and_after_job_construction(scene, monkeypatch):
    (scene.root / "pause.request").write_text("Pause")
    assert scene.controller.run(scene.root)["status"] == "paused"
    assert scene.calls == []
    (scene.root / "pause.request").unlink()
    build = scene.contracts.build_job

    def pause_during_build(*args, **kwargs):
        job = build(*args, **kwargs)
        (scene.root / "pause.request").write_text("Pause")
        return job

    monkeypatch.setattr(scene.contracts, "build_job", pause_during_build)
    assert scene.controller.run(scene.root)["status"] == "paused"
    assert scene.calls == []


def test_changed_frozen_settings_locator_cannot_launch(scene):
    scene.write(scene.root / "settings-identity.json", dict(path="replacement-settings.json"))
    with pytest.raises(ValueError, match="Frozen instance settings changed"):
        scene.controller.run(scene.root)
    assert scene.calls == []
    assert not (scene.root / "host-started.json").exists()


def _hold_writer(controller, root, ready, release):
    with controller._writer(root):
        ready.set()
        release.wait(10)


@pytest.mark.process_e2e
def test_concurrent_controller_is_rejected_by_process_lock(scene):
    context = multiprocessing.get_context("fork")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_hold_writer,
        args=(scene.controller, scene.root, ready, release))
    process.start()
    try:
        assert ready.wait(5)
        with pytest.raises(RuntimeError, match="already has a running controller"):
            scene.controller.run(scene.root)
        assert scene.calls == []
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0


@pytest.mark.parametrize("max_tokens,max_seconds,max_steps", [
    (0, 60, 1), (100, float("inf"), 1), (100, 60, True), (100, -1, 1)])
def test_prepare_requires_explicit_finite_budgets(scene, tmp_path, max_tokens, max_seconds, max_steps):
    with pytest.raises(ValueError):
        scene.controller.prepare(tmp_path / "invalid", tmp_path / "objective.txt", tmp_path / "public",
            ["fake-launcher"], max_tokens=max_tokens, max_seconds=max_seconds,
            max_steps=max_steps, model="fake-model", reasoning_effort="high")
    assert not (tmp_path / "invalid").exists()


def _w_payload(**changes):
    return dict(dict(summary='Bounded result', scientific_result='A scientific observation',
        limitations=['Scope is bounded'], implementation_selection=None, consultation_question=None,
        stage_intent='decision', mechanism_findings=None, candidate_nominations=None), **changes)


def _accepted_native(contracts, root, step, record):
    from scidiscovery.research_loop import accepted_budget
    intent = dict(task_ref=contracts.record_ref(root / 'jobs' / f'{step:03d}.json'))
    contracts.write(record / 'native-intent.json', intent)
    accepted_budget.accept_native(root, step, record, intent)


@pytest.fixture
def real_node(tmp_path, monkeypatch):
    controller = importlib.import_module('scidiscovery.research_loop.controller')
    contracts = importlib.import_module('scidiscovery.research_loop.contracts')
    native = importlib.import_module('scidiscovery.research_loop.native')
    root = tmp_path / 'research'
    objective = tmp_path / 'objective.md'
    objective.write_text('Resolve the original mechanism')
    supplement = tmp_path / 'update.md'
    supplement.write_text('Preserve the original temperature constraint')
    public = tmp_path / 'public'
    public.mkdir()
    controller.prepare(root, objective, public, ['fake'], max_tokens=1000, max_seconds=60,
        max_steps=3, model='fake', reasoning_effort='high', user_updates=[supplement],
        max_output_item_bytes=64, max_output_total_bytes=96)
    job = contracts.build_job(root, 1, 'W', [])
    work = contracts.workspace(root, 1)
    record = contracts.host_record(work)
    _accepted_native(contracts, root, 1, record)
    contracts.write(record / 'science-output.json', _w_payload())
    contracts.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=0, output_tokens=3))
    contracts.write(record / 'native-outcome.json',
        dict(returncode=0, stop_reason=None, native_started=False, usage=native.usage(root),
            raw_ref=contracts.file_ref(record / 'science-output.json')))
    return SimpleNamespace(controller=controller, contracts=contracts, native=native,
        root=root, work=work, record=record, job=job, objective=objective, supplement=supplement)


def test_nested_experiment_deliverables_exclude_scratch_and_need_no_manifest(real_node):
    n = real_node
    (n.work / 'work/large-model.bin').write_bytes(b'x' * 1024)
    experiment = n.work / 'experiments/sweep'
    (experiment / 'work').mkdir(parents=True)
    (experiment / 'work/raw.csv').write_bytes(b'x' * 1024)
    (experiment / 'artifacts').mkdir()
    (experiment / 'artifacts/measurements.csv').write_text('temperature,response\n300,1\n')
    (experiment / 'conclusion.md').write_text('A bounded experiment finding')
    n.contracts.seal_result(n.root, 1)
    child = n.contracts.build_job(n.root, 2, 'W', [1])
    view = n.contracts.read(n.contracts.workspace(n.root, 2) / 'science-task.json')
    assert view['global'] == dict(objective='inputs/global/objective.md',
        updates=['inputs/global/updates/001.md'])
    alias = view['parents'][0].pop('history_alias')
    assert alias.startswith('inputs/history/nodes/') and alias.endswith('/001/science.json')
    assert view['parents'] == [dict(conclusion='inputs/parents/001/conclusion.json',
        artifacts='inputs/parents/001/artifacts', role='W', limitations=['Scope is bounded'],
        limitations_omitted_count=0)]
    assert 'materials' not in view and 'goal_text' not in view and 'result_schema' not in view
    assert 'sha256' not in json.dumps(view)
    assert [Path(row['path']).name for row in child['direct_materials']] == ['measurements.csv', 'conclusion.md']
    assert not (n.work / 'result.json').exists()
    assert not (n.work / 'science-result.schema.json').exists()
    assert not list(n.work.rglob('*manifest*'))
    child_work = n.contracts.workspace(n.root, 2)
    assert not list((child_work / 'inputs').rglob('raw.csv'))
    assert not list((child_work / 'inputs').rglob('large-model.bin'))
    child_record = n.contracts.host_record(child_work)
    _accepted_native(n.contracts, n.root, 2, child_record)
    n.contracts.write(child_record / 'science-output.json', _w_payload(summary='Local W finding',
        scientific_result='No further selected output', limitations=['One experiment']))
    n.contracts.write(child_record / 'native-outcome.json', dict(returncode=0, stop_reason=None,
        native_started=False, usage=dict(unknown_steps=[]), raw_ref=n.contracts.file_ref(child_record / 'science-output.json')))
    n.contracts.seal_result(n.root, 2)
    grandchild = n.contracts.build_job(n.root, 3, 'A', [2])
    # W-to-W relays keep the exact selected deliverables, never scratch or ancestry conclusions.
    assert [Path(row['path']).name for row in grandchild['direct_materials']] == ['measurements.csv', 'conclusion.md']
    assert not any('/work/' in row['path'] for row in grandchild['direct_materials'])
    assert [row['path'] for row in grandchild['direct_refs']] == ['inputs/parents/002/conclusion.json']
    assert not (n.contracts.workspace(n.root, 3) / 'inputs/parents/001').exists()


@pytest.mark.parametrize('target', ['objective', 'supplement'])
def test_changed_global_context_cannot_be_sealed(real_node, target):
    n = real_node
    path = getattr(n, target)
    path.chmod(0o600)
    path.write_text('A silently replaced, longer global constraint')
    with pytest.raises(ValueError):
        n.contracts.seal_result(n.root, 1)
    assert not (n.record / 'result-identity.json').exists()


@pytest.mark.parametrize('kind', ['artifact_link', 'experiment_link', 'collision'])
def test_unsafe_or_colliding_deliverable_paths_are_rejected(real_node, kind):
    n = real_node
    if kind == 'artifact_link':
        (n.work / 'artifacts/result.csv').symlink_to(n.root / 'settings.json')
    elif kind == 'experiment_link':
        (n.work / 'experiments').mkdir()
        (n.work / 'experiments/linked').symlink_to(n.work / 'work', target_is_directory=True)
    else:
        for path in (n.work / 'artifacts/experiments/test/artifacts/a.txt',
                     n.work / 'experiments/test/artifacts/a.txt'):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('Selected finding')
    with pytest.raises(ValueError):
        n.contracts.seal_result(n.root, 1)
    assert not (n.record / 'result-identity.json').exists()


def _record_launches(n, monkeypatch):
    launches = []
    monkeypatch.setattr(n.controller, 'native', SimpleNamespace(usage=n.native.usage,
        wait_native=n.native.wait_native, execute_native=lambda *args: launches.append(args)))
    return launches


def test_terminal_success_sealing_recovery_never_relaunches_science(real_node, monkeypatch):
    n = real_node
    artifact = n.work / 'artifacts/model.bin'
    artifact.symlink_to(n.root / 'settings.json')
    with pytest.raises(ValueError):
        n.contracts.seal_result(n.root, 1)
    assert not (n.record / 'result-identity.json').exists()
    # An explicit operator repair removes the unsafe deliverable. The retained
    # scientific JSON and the original native launch remain unchanged.
    original_science = (n.record / 'science-output.json').read_bytes()
    artifact.unlink()
    launches = _record_launches(n, monkeypatch)
    (n.root / 'pause.request').write_text('Stop after recovering the retained node')
    assert n.controller.run(n.root)['status'] == 'paused'
    assert not launches
    assert (n.record / 'science-output.json').read_bytes() == original_science
    assert n.contracts.read(n.record / 'result-identity.json')['materials'] == []


@pytest.mark.parametrize('change', ['failed', 'stopped', 'invalid_science'])
def test_ineligible_native_work_cannot_use_sealing_recovery(real_node, monkeypatch, change):
    n = real_node
    outcome = n.contracts.read(n.record / 'native-outcome.json')
    if change == 'failed':
        outcome['returncode'] = 1
    elif change == 'stopped':
        outcome['stop_reason'] = 'wall_limit'
    else:
        n.contracts.write(n.record / 'science-output.json', {'summary': 'Incomplete'})
        outcome['raw_ref'] = n.contracts.file_ref(n.record / 'science-output.json')
    n.contracts.write(n.record / 'native-outcome.json', outcome)
    launches = _record_launches(n, monkeypatch)
    expected = 'output_repair_required' if change == 'invalid_science' else 'native_outcome_unsealed'
    result = n.controller.run(n.root)
    assert result['status'] == expected
    if change == 'invalid_science':
        assert result['reason'] == 'invalid_fields'
        assert not (n.record / 'continuations').exists()
    assert not launches
    assert not (n.record / 'result-identity.json').exists()


def test_changed_terminal_payload_cannot_be_resealed(real_node, monkeypatch):
    n = real_node
    payload = n.contracts.read(n.record / 'science-output.json')
    n.contracts.write(n.record / 'science-output.json', dict(payload, stage_intent='review'))
    launches = _record_launches(n, monkeypatch)
    assert n.controller.run(n.root)['status'] == 'native_outcome_unsealed'
    assert not launches
    assert not (n.record / 'result-identity.json').exists()


def _set_terminal_science(node, text):
    payload = node.contracts.read(node.record / 'science-output.json')
    node.contracts.write(node.record / 'science-output.json', dict(payload, scientific_result=text))
    outcome = node.contracts.read(node.record / 'native-outcome.json')
    node.contracts.write(node.record / 'native-outcome.json', dict(outcome,
        raw_ref=node.contracts.file_ref(node.record / 'science-output.json')))


def test_scientific_markdown_links_become_direct_readable_entry_points(real_node):
    n = real_node
    selected = n.work / 'experiments/sweep/artifacts/measurements.csv'
    selected.parent.mkdir(parents=True)
    selected.write_text('temperature,response\n300,1\n')
    _set_terminal_science(n, 'The [selected measurements](experiments/sweep/artifacts/measurements.csv) '
        'support the observation. Compare [source](https://example.org/paper).')
    original = (n.record / 'science-output.json').read_bytes()
    n.contracts.seal_result(n.root, 1)
    n.contracts.build_job(n.root, 2, 'W', [1])
    child = n.contracts.workspace(n.root, 2)
    parent = n.contracts.read(child / 'science-task.json')['parents'][0]
    assert parent['evidence'] == [dict(label='selected measurements',
        path='inputs/parents/001/artifacts/experiments/sweep/artifacts/measurements.csv')]
    child_job = n.contracts.read_job(n.root / 'jobs/002.json')
    assert any(row['path'].endswith('experiments/sweep/artifacts/measurements.csv')
        and row['size_bytes'] == selected.stat().st_size for row in child_job['direct_materials']), child_job['direct_materials']
    assert (n.record / 'science-output.json').read_bytes() == original


@pytest.mark.parametrize('target', ['artifacts/missing.csv', 'artifacts/../work/model.bin',
    'artifacts/%2e%2e/work/model.bin', 'experiments/sweep/work/raw.csv'])
def test_scientific_links_must_name_exact_selected_deliverables(real_node, target):
    n = real_node
    scratch = n.work / 'experiments/sweep/work/raw.csv'
    scratch.parent.mkdir(parents=True)
    scratch.write_text('Intermediate data')
    _set_terminal_science(n, f'The [measurements]({target}) support this finding.')
    n.contracts.seal_result(n.root, 1)
    identity = n.contracts.read(n.record / 'result-identity.json')
    assert identity['entry_points'] == []
    assert len(identity['unresolved_links']['items']) == 1


def test_many_scientific_links_validate_all_and_bound_visible_entry_points(real_node):
    n = real_node
    for index in range(20):
        (n.work / 'artifacts' / f'{index}.csv').write_text('')
    links = ' '.join(f'[measurement {index}](artifacts/{index}.csv)' for index in range(20))
    _set_terminal_science(n, links)
    n.contracts.seal_result(n.root, 1)
    assert len(n.contracts.read(n.record / 'result-identity.json')['entry_points']) == 20
    n.contracts.build_job(n.root, 2, 'W', [1])
    parent = n.contracts.read(n.contracts.workspace(n.root, 2) / 'science-task.json')['parents'][0]
    assert len(parent['evidence']) == 16
    assert parent['evidence_omitted_count'] == 4


def test_missing_late_link_is_checked_beyond_visible_entry_point_limit(real_node):
    n = real_node
    for index in range(17):
        (n.work / 'artifacts' / f'{index}.csv').write_text('')
    links = ' '.join(f'[measurement {index}](artifacts/{index}.csv)' for index in range(18))
    _set_terminal_science(n, links)
    n.contracts.seal_result(n.root, 1)
    identity = n.contracts.read(n.record / 'result-identity.json')
    assert len(identity['entry_points']) == 17
    assert len(identity['unresolved_links']['items']) == 1
