"""Scientific routing and recovery controls, without native model execution."""
from __future__ import annotations

import importlib
import json
import multiprocessing
from pathlib import Path
import shutil
import sys
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def scene(tmp_path, monkeypatch):
    # Controller dependencies are owned separately; the fake implements their
    # transport contract and retains byte identities for recovery checks.
    for name in ("contracts", "native"):
        full = "scidiscovery.research_loop." + name
        if importlib.util.find_spec(full) is None:
            monkeypatch.setitem(sys.modules, full, ModuleType(full))
    controller = importlib.import_module("scidiscovery.research_loop.controller")

    def read(path):
        return json.loads(Path(path).read_text())

    def write(path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    reference_api = importlib.import_module("scidiscovery.research_loop.contracts")
    record_ref, file_ref = reference_api.record_ref, reference_api.file_ref

    def workspace(root, step):
        return root / "project/nodes" / f"{step:03d}"

    def build_job(root, step, role, parents, instruction="", evidence_return=None, correction_request=None):
        assert correction_request is None  # This legacy-policy fixture has no N4 request.
        settings = read(root / "settings.json")
        work = workspace(root, step)
        work.mkdir(parents=True, exist_ok=True)
        refs = []
        for parent in parents:
            source = root / "native-evidence" / f"{parent:03d}" / "science-output.json"
            ref = dict(path=f"inputs/parents/{parent:03d}/conclusion.json", size_bytes=source.stat().st_size)
            destination = work / ref["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            refs.append(ref)
        job = dict(step=step, role=role, goal_text=settings["objective"],
            public_root=settings["public_root"], direct_refs=refs,
            instruction=instruction, evidence_return=evidence_return)
        authority = root / "jobs" / f"{step:03d}.json"
        if authority.exists() and read(authority) != job:
            raise ValueError("Job changed")
        write(authority, job)
        return job

    root = tmp_path / "research"

    def validate(work):
        identity = read(root / "native-evidence" / work.name / "result-identity.json")
        if identity["result_ref"] != file_ref(root / "native-evidence" / work.name / "result.json"):
            raise ValueError("Result changed")
        if identity["raw_ref"] != file_ref(root / "native-evidence" / work.name / "science-output.json"):
            raise ValueError("Original science changed")
        job = read(root / "jobs" / (work.name + ".json"))
        if identity["job_ref"] != record_ref(root / "jobs" / (work.name + ".json")):
            raise ValueError("Job changed")
        result = read(root / "native-evidence" / work.name / "result.json")
        assert result["role"] == job["role"]
        assert result["direct_refs"] == job["direct_refs"]
        for ref in job["direct_refs"]:
            if (work / ref["path"]).stat().st_size != ref["size_bytes"]:
                raise ValueError("Direct original changed")
        return result

    calls = []
    plan = []

    def usage(root):
        intents = sorted((root / "native-evidence").glob("*/native-intent.json"))
        unknown = [int(path.parent.name) for path in intents
                   if not (path.parent / "native-outcome.json").exists()]
        known = len(intents) - len(unknown)
        return dict(observed_tokens=known * 10, unknown_steps=unknown,
                    input_tokens=known * 7, cached_input_tokens=0, output_tokens=known * 3)

    def seal_result(root, step):
        work = workspace(root, step)
        record = root / 'native-evidence' / work.name
        job_path = root / 'jobs' / f'{step:03d}.json'
        job = read(job_path)
        payload = read(record / 'science-output.json')
        result = dict(payload, role=job['role'], direct_refs=job['direct_refs'],
            next_action=payload.get('next_action', ''), route_reason=payload.get('route_reason', ''))
        write(record / 'result.json', result)
        write(record / 'result-identity.json', dict(result_ref=file_ref(record / 'result.json'),
            job_ref=record_ref(job_path), raw_ref=file_ref(record / 'science-output.json'),control_outcome={}))
        return validate(work)

    def execute_native(root, step):
        calls.append(step)
        job_path = root / "jobs" / f"{step:03d}.json"
        job = read(job_path)
        role, action = plan[step - 1]
        assert role == job["role"]
        record = root / "native-evidence" / f"{step:03d}"
        write(record / "native-intent.json", dict(step=step, task_ref=record_ref(job_path)))
        result = dict(role=role, next_action=action, route_reason="Evidence requests this route" if action else "",
            direct_refs=job["direct_refs"], summary=f"Contribution {step}",
            scientific_result="A bounded finding", limitations=["Goal is not fully tested"])
        payload = {key: value for key, value in result.items() if key not in {"role", "direct_refs"}}
        if role in {"W", "E"}:
            payload.pop("next_action")
            payload.pop("route_reason")
        write(record / "science-output.json", payload)
        work = workspace(root, step)
        write(root / "native-evidence" / work.name / "result.json", result)
        write(record / "native-outcome.json", dict(returncode=0, stop_reason=None,
            usage=dict(observed_tokens=10, unknown_steps=[])))
        write(record / "result-identity.json", dict(result_ref=file_ref(root / "native-evidence" / work.name / "result.json"),
            job_ref=record_ref(job_path), raw_ref=file_ref(record / "science-output.json"),control_outcome={}))

    contracts = SimpleNamespace(read=read, write=write, record_ref=record_ref, file_ref=file_ref,
        record_path=reference_api.record_path,
        workspace=workspace, build_job=build_job, validate=validate,
        seal_result=seal_result,
        regular=importlib.import_module('scidiscovery.research_loop.contracts').regular,
        settings=importlib.import_module('scidiscovery.research_loop.contracts').settings)
    native = SimpleNamespace(execute_native=execute_native, usage=usage)
    monkeypatch.setattr(controller, "contracts", contracts)
    monkeypatch.setattr(controller, "native", native)
    objective = tmp_path / "objective.txt"
    objective.write_text("Measure the unresolved mechanism")
    public = tmp_path / "public"
    public.mkdir()
    controller.prepare(root, objective, public, ["fake-launcher"], max_tokens=1000,
        max_seconds=60, max_steps=20, model="fake-model", reasoning_effort="high", search_policy=None)

    return SimpleNamespace(controller=controller, root=root, plan=plan, calls=calls,
        read=read, write=write, workspace=workspace, native=native)


def job(scene, step):
    return scene.read(scene.root / "jobs" / f"{step:03d}.json")


def parents(scene, step):
    return [int(Path(ref["path"]).parent.name) for ref in job(scene, step)["direct_refs"]]


def test_h_to_a_and_first_experiment_from_a(scene):
    scene.plan[:] = [("H", "analyze"), ("A", "continue_w"), ("W", ""), ("A", "finish")]
    result = scene.controller.run(scene.root)
    assert scene.calls == [1, 2, 3, 4]
    assert parents(scene, 2) == [1]
    assert parents(scene, 3) == [1, 2]
    assert parents(scene, 4) == [1, 3]
    assert result["status"] == "finish"
    assert result["goal_completion"] == "not_asserted"
    assert result["unmet_goal"] == "Measure the unresolved mechanism"
    assert result["limitations"] == ["Goal is not fully tested"]


def test_extractions_return_to_requester_and_preserve_h_w(scene):
    scene.plan[:] = [("H", "experiment"), ("W", ""), ("A", "extract"), ("E", ""),
        ("A", "revise_h"), ("H", "extract"), ("E", ""), ("H", "analyze"), ("A", "finish")]
    assert scene.controller.run(scene.root)["status"] == "finish"
    assert job(scene, 4)["evidence_return"] == {"role": "A", "step": 3}
    assert parents(scene, 5) == [1, 2, 3, 4]
    assert parents(scene, 6) == [1, 2, 5]
    assert job(scene, 7)["evidence_return"] == {"role": "H", "step": 6}
    assert parents(scene, 8) == [6, 2, 7]
    assert parents(scene, 9) == [8, 2]
    assert len(parents(scene, 8)) == 3  # No recursive ancestry inventory.


def test_restart_reuses_only_complete_sealed_nodes(scene):
    scene.plan[:] = [("H", "experiment"), ("W", ""), ("A", "finish")]
    original = scene.native.execute_native

    def pause_after_h(root, step):
        original(root, step)
        if step == 1:
            (root / "pause.request").write_text("Pause before further work")

    scene.native.execute_native = pause_after_h
    assert scene.controller.run(scene.root)["status"] == "paused"
    start = scene.read(scene.root / "host-started.json")
    (scene.root / "pause.request").unlink()
    assert scene.controller.run(scene.root)["status"] == "finish"
    assert scene.calls == [1, 2, 3]
    assert scene.read(scene.root / "host-started.json") == start
    assert scene.controller.run(scene.root)["status"] == "finish"
    assert scene.calls == [1, 2, 3]


@pytest.mark.parametrize("known_outcome", [False, True])
def test_unsealed_original_is_never_replayed(scene, known_outcome):
    scene.plan[:] = [("H", "experiment")]
    record = scene.root / "native-evidence/001"
    scene.write(record / "native-intent.json", {"step": 1})
    if known_outcome:
        scene.write(record / "native-outcome.json", dict(returncode=0, stop_reason=None,
            usage=dict(unknown_steps=[])))
    assert scene.controller.run(scene.root)["status"] == "native_outcome_unsealed"
    assert scene.calls == []


def test_native_failure_stops_without_a_replacement(scene):
    def crash(root, step):
        scene.calls.append(step)
        scene.write(root / "native-evidence/001/native-intent.json", {"step": step})
        raise RuntimeError("Submission acceptance was lost")

    scene.native.execute_native = crash
    assert scene.controller.run(scene.root)["status"] == "native_stopped"
    assert scene.controller.run(scene.root)["status"] == "native_outcome_unsealed"
    assert scene.calls == [1]


def test_tampered_sealed_result_cannot_route(scene):
    scene.plan[:] = [("H", "wait")]
    assert scene.controller.run(scene.root)["status"] == "wait"
    output = scene.root / "native-evidence/001/result.json"
    modified = scene.read(output)
    modified["next_action"] = "experiment"
    scene.write(output, modified)
    assert scene.controller.run(scene.root)["status"] == "sealed_node_invalid"
    assert scene.calls == [1]


def test_pause_before_launch_and_after_job_construction(scene):
    (scene.root / "pause.request").write_text("Pause")
    assert scene.controller.run(scene.root)["status"] == "paused"
    assert scene.calls == []
    (scene.root / "pause.request").unlink()
    build = scene.controller.contracts.build_job

    def pause_during_build(*args, **kwargs):
        job = build(*args, **kwargs)
        (scene.root / "pause.request").write_text("Pause")
        return job

    scene.controller.contracts.build_job = pause_during_build
    assert scene.controller.run(scene.root)["status"] == "paused"
    assert scene.calls == []


@pytest.mark.parametrize("changed", [dict(max_tokens=2000), dict(model="replacement-model")])
def test_changed_frozen_settings_cannot_launch(scene, changed):
    settings = scene.read(scene.root / "settings.json")
    scene.write(scene.root / "settings.json", dict(settings, **changed))
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


def test_real_sealing_and_materials_compose_with_controller(tmp_path, monkeypatch):
    controller = importlib.import_module("scidiscovery.research_loop.controller")
    contracts = importlib.import_module("scidiscovery.research_loop.contracts")
    native = importlib.import_module("scidiscovery.research_loop.native")
    monkeypatch.setattr(controller, "contracts", contracts)
    root = tmp_path / "research"
    objective = tmp_path / "objective.txt"
    objective.write_text("Resolve an open scientific question")
    public = tmp_path / "public"
    public.mkdir()
    controller.prepare(root, objective, public, ["fake-launcher"], max_tokens=1000,
        max_seconds=60, max_steps=5, model="fake", reasoning_effort="high", search_policy=None)
    plan = [("H", "experiment"), ("W", ""), ("A", "extract"), ("E", ""), ("A", "finish")]
    calls = []

    def execute(root, step):
        calls.append(step)
        role, action = plan[step - 1]
        work = contracts.workspace(root, step)
        job_path = root / "jobs" / f"{step:03d}.json"
        assert contracts.read(job_path)["role"] == role
        record = contracts.host_record(work)
        contracts.write(record / "native-intent.json", dict(task_ref=contracts.record_ref(job_path)))
        payload = dict(summary=f"Original contribution {step}", scientific_result="Bounded evidence",
            limitations=["Unobserved goal conditions"])
        if contracts.read(job_path).get('handoff_protocol', 1) >= 7:
            payload['implementation_selection'] = None
        if role in {"H", "A"}:
            payload.update(next_action=action, route_reason="The direct evidence supports this route")
        if role == 'A':
            payload['candidate_recommendation'] = None
            payload['progress_assessment'] = None
            payload['decision_request'] = None
            payload['portfolio_review'] = None
        if role == 'W':
            payload['stage_intent'] = 'review'
            payload['mechanism_findings'] = None
        if role in {'H', 'A'}:
            payload['search_plan' if role == 'H' else 'search_review'] = None
        artifacts = work / "artifacts"
        artifacts.mkdir(exist_ok=True)
        (artifacts / f"evidence-{step}.txt").write_text(f"Original {role} observation")
        contracts.write(record / "science-output.json", payload)
        contracts.write(record / "usage.json", dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
        contracts.write(record / "native-outcome.json", dict(returncode=0, stop_reason=None,
            usage=native.usage(root), raw_ref=contracts.file_ref(record / 'science-output.json')))
        contracts.seal_result(root, step)

    monkeypatch.setattr(controller, "native", SimpleNamespace(execute_native=execute, usage=native.usage))
    result = controller.run(root)
    assert result["status"] == "finish"
    assert result["usage"]["observed_tokens"] == 50
    assert result["usage"]["cached_input_tokens"] == 10
    final_job = contracts.read(root / "jobs/005.json")
    assert [item["path"] for item in final_job["direct_refs"]] == [
        "inputs/parents/001/conclusion.json", "inputs/parents/002/conclusion.json",
        "inputs/parents/003/conclusion.json", "inputs/parents/004/conclusion.json"]
    assert len(final_job["direct_materials"]) == 4
    for item in final_job["direct_refs"]:
        payload = contracts.read(contracts.workspace(root, 5) / item["path"])
        assert "role" not in payload
        assert "direct_refs" not in payload
    assert controller.run(root)["status"] == "finish"
    assert calls == [1, 2, 3, 4, 5]


@pytest.fixture
def real_node(tmp_path, monkeypatch, request):
    controller = importlib.import_module('scidiscovery.research_loop.controller')
    contracts = importlib.import_module('scidiscovery.research_loop.contracts')
    native = importlib.import_module('scidiscovery.research_loop.native')
    monkeypatch.setattr(controller, 'contracts', contracts)
    monkeypatch.setattr(contracts, 'HANDOFF_PROTOCOL', getattr(request, 'param', 7))
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
    job = contracts.build_job(root, 1, 'H', [])
    work = contracts.workspace(root, 1)
    record = contracts.host_record(work)
    contracts.write(record / 'native-intent.json',
        dict(task_ref=contracts.record_ref(root / 'jobs/001.json')))
    contracts.write(record / 'science-output.json', dict(summary='Bounded result',
        scientific_result='A scientific observation', limitations=['Scope is bounded'],
        next_action='wait', route_reason='Await further measurements', search_plan=None,
        **(dict(implementation_selection=None) if job['handoff_protocol'] >= 7 else {})))
    contracts.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=0, output_tokens=3))
    contracts.write(record / 'native-outcome.json',
        dict(returncode=0, stop_reason=None, usage=native.usage(root), raw_ref=contracts.file_ref(record / 'science-output.json')))
    return SimpleNamespace(controller=controller, contracts=contracts, native=native,
        root=root, work=work, record=record, job=job)


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
    assert view['parents'] == [dict(conclusion='inputs/parents/001/conclusion.json',
        artifacts='inputs/parents/001/artifacts', role='H', limitations=['Scope is bounded'],
        limitations_omitted_count=0)]
    assert 'materials' not in view and 'goal_text' not in view and 'result_schema' not in view
    assert 'sha256' not in json.dumps(view)
    assert [Path(row['path']).name for row in child['direct_materials']] == ['measurements.csv', 'conclusion.md']
    assert not (n.work / 'result.json').exists()
    assert not (n.work / 'science-result.schema.json').exists()
    assert 'direct_refs' not in n.contracts.read(n.work / 'conclusion.json')
    assert not list(n.work.rglob('*manifest*'))
    child_work = n.contracts.workspace(n.root, 2)
    assert not list((child_work / 'inputs').rglob('raw.csv'))
    assert not list((child_work / 'inputs').rglob('large-model.bin'))
    child_record = n.contracts.host_record(child_work)
    n.contracts.write(child_record / 'native-intent.json',
        dict(task_ref=n.contracts.record_ref(n.root / 'jobs/002.json')))
    n.contracts.write(child_record / 'science-output.json', dict(summary='Local W finding',
        scientific_result='No further selected output', limitations=['One experiment'],
        implementation_selection=None, stage_intent='review', mechanism_findings=None))
    n.contracts.write(child_record / 'native-outcome.json', dict(returncode=0, stop_reason=None,
        usage=dict(unknown_steps=[]), raw_ref=n.contracts.file_ref(child_record / 'science-output.json')))
    n.contracts.seal_result(n.root, 2)
    grandchild = n.contracts.build_job(n.root, 3, 'A', [2])
    assert grandchild['direct_materials'] == []
    assert [row['path'] for row in grandchild['direct_refs']] == ['inputs/parents/002/conclusion.json']
    assert not (n.contracts.workspace(n.root, 3) / 'inputs/parents/001').exists()


@pytest.mark.parametrize('target', ['project/global/objective.md', 'project/global/updates/001.md',
    'project/nodes/001/inputs/global/updates/001.md'])
def test_changed_global_context_cannot_be_sealed(real_node, target):
    n = real_node
    path = n.root / target
    path.chmod(0o600)
    path.write_text('A silently replaced constraint')
    message=('The global context read by this role changed'
        if target=='project/nodes/001/inputs/global/updates/001.md' else 'Bound global original changed')
    with pytest.raises(ValueError, match='^'+message+'$'):
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


def test_terminal_success_sealing_recovery_never_relaunches_science(real_node, monkeypatch):
    n = real_node
    artifact = n.work / 'artifacts/model.bin'
    artifact.write_bytes(b'x' * 100)
    with pytest.raises(ValueError, match='byte budget'):
        n.contracts.seal_result(n.root, 1)
    assert not (n.record / 'result-identity.json').exists()
    # An explicit operator repair moves intermediate data into scratch. The
    # retained scientific JSON and the original native launch remain unchanged.
    original_science = (n.record / 'science-output.json').read_bytes()
    artifact.rename(n.work / 'work/model.bin')
    launches = []
    monkeypatch.setattr(n.controller, 'native', SimpleNamespace(
        usage=n.native.usage, execute_native=lambda *args: launches.append(args)))
    assert n.controller.run(n.root)['status'] == 'wait'
    assert not launches
    assert (n.record / 'science-output.json').read_bytes() == original_science
    assert n.contracts.read(n.record / 'result-identity.json')['materials'] == []


@pytest.mark.parametrize('change', ['failed', 'stopped', 'usage_unknown', 'invalid_science'])
def test_ineligible_native_work_cannot_use_sealing_recovery(real_node, monkeypatch, change):
    n = real_node
    outcome = n.contracts.read(n.record / 'native-outcome.json')
    if change == 'failed':
        outcome['returncode'] = 1
    elif change == 'stopped':
        outcome['stop_reason'] = 'wall_limit'
    elif change == 'usage_unknown':
        outcome['usage']['unknown_steps'] = [1]
        outcome['usage']['blocking_unknown_steps'] = [1]
    else:
        n.contracts.write(n.record / 'science-output.json', {'summary': 'Incomplete'})
        outcome['raw_ref'] = n.contracts.file_ref(n.record / 'science-output.json')
    n.contracts.write(n.record / 'native-outcome.json', outcome)
    launches = []
    monkeypatch.setattr(n.controller, 'native', SimpleNamespace(
        usage=n.native.usage, execute_native=lambda *args: launches.append(args)))
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
    n.contracts.write(n.record / 'science-output.json', dict(payload, next_action='experiment'))
    launches = []
    monkeypatch.setattr(n.controller, 'native', SimpleNamespace(
        usage=n.native.usage, execute_native=lambda *args: launches.append(args)))
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
    assert (child / parent['evidence'][0]['path']).read_text() == selected.read_text()
    assert (n.record / 'science-output.json').read_bytes() == original


@pytest.mark.parametrize('target', ['artifacts/missing.csv', 'artifacts/../work/model.bin',
    'artifacts/%2e%2e/work/model.bin', 'experiments/sweep/work/raw.csv'])
@pytest.mark.parametrize('real_node', [6, 7], indirect=True)
def test_scientific_links_must_name_exact_selected_deliverables(real_node, target):
    n = real_node
    scratch = n.work / 'experiments/sweep/work/raw.csv'
    scratch.parent.mkdir(parents=True)
    scratch.write_text('Intermediate data')
    _set_terminal_science(n, f'The [measurements]({target}) support this finding.')
    if n.job['handoff_protocol'] < 7:
        with pytest.raises(ValueError, match='artifact link'):
            n.contracts.seal_result(n.root, 1)
        assert not (n.record / 'result-identity.json').exists()
    else:
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


@pytest.mark.parametrize('real_node', [6, 7], indirect=True)
def test_missing_late_link_is_checked_beyond_visible_entry_point_limit(real_node):
    n = real_node
    for index in range(17):
        (n.work / 'artifacts' / f'{index}.csv').write_text('')
    links = ' '.join(f'[measurement {index}](artifacts/{index}.csv)' for index in range(18))
    _set_terminal_science(n, links)
    if n.job['handoff_protocol'] < 7:
        with pytest.raises(ValueError, match='selected deliverable'):
            n.contracts.seal_result(n.root, 1)
        assert not (n.record / 'result-identity.json').exists()
    else:
        n.contracts.seal_result(n.root, 1)
        identity = n.contracts.read(n.record / 'result-identity.json')
        assert len(identity['entry_points']) == 17
        assert len(identity['unresolved_links']['items']) == 1
