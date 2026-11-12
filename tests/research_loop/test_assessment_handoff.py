"""W consultation retains exact A originals without granting A routing authority."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c, controller, handoff_state, originals, shared_input_views
from test_direct_input_bindings import direct_instance, stopped_seals
from test_history_handoff import finish


@pytest.fixture
def scene(tmp_path, monkeypatch):
    root, _ = direct_instance(tmp_path)
    stopped_seals(monkeypatch)
    finish(root, 1, 'W', [], stage_intent='review', files={'observation.txt': 'Retained W observation'})
    return root


def advice(root, *, files=None, selection=None):
    requester = c.read_job(root / 'jobs/001.json')
    route = controller._route(root, 1, 'W', handoff_state.read_sealed(root, 1), requester, None, None)
    job = c.build_job(root, 2, route.role, list(route.parents), instruction=route.instruction,
        evidence_return=route.evidence_return)
    finish(root, 2, 'A', list(route.parents), files=files, selection=selection)
    (c.workspace(root, 2) / 'work/private-check.py').write_text(
        'This scratch file is not a retained scientific original')
    returned = controller._route(root, 2, 'A', handoff_state.read_sealed(root, 2), job,
        route.current_h, route.current_w)
    return c.build_job(root, 3, returned.role, list(returned.parents), instruction=returned.instruction,
        evidence_return=returned.evidence_return)


def assert_original(root, job, step):
    work = c.workspace(root, job['step'])
    path = f'inputs/parents/{step:03d}/conclusion.json'
    bound = next(row for row in job['direct_refs'] if row['path'] == path)
    actual = originals.resolve_root_ref(root, dict(bound, path=str(work / path)))
    expected = c._execution_raw(root, step)
    assert actual == expected
    argv = shared_input_views.mount_arguments(root, job, work, include_skills=False)
    assert any(argv[index:index + 3] == ['--ro-bind', str(expected), str(work / path)]
        for index in range(len(argv) - 2))
    assert not (work / path).exists()


def test_advice_returns_exact_requester_and_assessment_originals(scene):
    job = advice(scene)
    assert [ref['path'] for ref in job['direct_refs']] == [
        'inputs/parents/001/conclusion.json', 'inputs/parents/002/conclusion.json']
    assert job['worker_continuation']['requester_step'] == 1
    assert job['worker_continuation']['advice_step'] == 2
    assert_original(scene, job, 1)
    assert_original(scene, job, 2)
    assert not job.get('execution_qualifications')


def test_advice_declared_artifacts_are_reusable_but_work_scratch_is_not(scene):
    job = advice(scene, files={'check.py': 'print(42)\n', 'report.txt': 'A declared check result'},
        selection=['W999-I001'])
    author = c.workspace(scene, 2)
    scratch = author / 'work/private-check.py'
    materials = {row['path']: row for row in job['direct_materials']}
    declared = {'inputs/parents/002/artifacts/check.py', 'inputs/parents/002/artifacts/report.txt'}
    assert declared <= materials.keys()
    argv = shared_input_views.mount_arguments(scene, job, c.workspace(scene, 3), include_skills=False)
    for path in declared:
        actual = originals.material_path(scene, materials[path])
        assert actual == author / 'artifacts' / Path(path).name
        assert actual.stat().st_mode & 0o777 == 0o400
        assert any(argv[index:index + 3] == ['--ro-bind', str(actual), str(c.workspace(scene, 3) / path)]
            for index in range(len(argv) - 2))
    assert str(scratch) not in argv and str(author / 'work') not in argv
    # Advice cannot choose an implementation for W; W can explicitly select it.
    assert job.get('selected_inputs', []) == []
    finish(scene, 3, 'W', [1, 2], stage_intent='handoff', selection=['A002-I001'])
    successor = c.build_job(scene, 4, 'W', [3])
    assert originals.material_path(scene, successor['selected_inputs'][0]['material']) == author / 'artifacts/check.py'
    assert [row['material']['path'] for row in successor['selected_inputs']] == [
        'inputs/parents/002/artifacts/check.py']


def test_consultation_job_and_public_view_stay_frozen(scene):
    job = advice(scene)
    paths = [scene / 'jobs/003.json', c.workspace(scene, 3) / 'science-task.json']
    frozen = [path.read_bytes() for path in paths]
    assert c.build_job(scene, 3, 'W', [1, 2], instruction=job['instruction']) == job
    assert [path.read_bytes() for path in paths] == frozen


@pytest.mark.parametrize('field,value', [
    ('purpose', 'closeout'), ('final_report', {'source_cutoff': 2}), ('final_only', True),
    ('formal_review', {'subject': 2}), ('author_correction', {'root': 3}),
    ('correction_request', {'source': 3}), ('fork_source', {'step': 2}),
])
def test_special_assessments_keep_their_original_contract(field, value):
    job = dict(role='A', step=3, **{field: value})
    assert c._assessment_handoff(job, sources={}, members={}, policy=None) is None


def test_changed_consultation_source_and_public_view_are_rejected(scene):
    job = advice(scene)
    work = c.workspace(scene, 3)
    changed = c.read(work / 'science-task.json')
    changed['parents'][0]['conclusion'] = 'inputs/parents/999/conclusion.json'
    c.write(work / 'science-task.json', changed)
    with pytest.raises(ValueError):
        c._validate_task_view(scene, work, job)
    c.write(work / 'science-task.json', c.science_view(job, scene))
    job['direct_refs'][1]['path'] = 'inputs/parents/001/conclusion.json'
    with pytest.raises(ValueError):
        c._validate_inputs(scene, work, job)


def test_changed_advice_declared_material_is_rejected(scene):
    job = advice(scene, files={'check.py': 'print(42)\n'})
    original = c.workspace(scene, 2) / 'artifacts/check.py'
    original.chmod(0o600)
    original.write_text('print(43)\n')
    with pytest.raises(ValueError, match='changed|identity|original'):
        shared_input_views.mount_arguments(scene, job, c.workspace(scene, 3), include_skills=False)


def test_requester_and_advice_materials_both_reach_returned_w(scene):
    job = advice(scene, files={'check.py': 'x' * 63, 'small-report.txt': 'x'})
    assert {row['path'] for row in job['direct_materials']} == {
        'inputs/parents/001/artifacts/observation.txt',
        'inputs/parents/002/artifacts/check.py',
        'inputs/parents/002/artifacts/small-report.txt'}
    assert_original(scene, job, 2)
    c._validate_inputs(scene, c.workspace(scene, 3), job)


def test_selected_fork_retains_advice_declared_originals(scene):
    from scidiscovery.research_loop import fork_runtime, fork_service

    source_job = advice(scene, files={'check.py': 'print(42)\n', 'report.txt': 'Retained check'})
    author = c.workspace(scene, 2)
    scratch = author / 'work/private-check.py'
    finish(scene, 3, 'W', [1, 2], stage_intent='handoff')
    for step in (1, 2, 3):
        (c.host_record(c.workspace(scene, step)) / 'runner.lock').touch(exist_ok=True)
    with controller._writer(scene):
        pass
    child = scene.parent / 'fork-child'
    limits = dict(max_tokens=1000, max_seconds=30, max_steps=1)
    preview = fork_service.preview(scene, 3, child, budget=limits)
    fork_service.create(scene, 3, child, budget=limits, expected_preview_id=preview['preview_id'],
        operation_id='assessment-declared-originals')
    selected = fork_runtime.build_selected_job(child, 3)
    assert selected['direct_materials'] == source_job['direct_materials']
    argv = shared_input_views.mount_arguments(child, selected, c.workspace(child, 3), include_skills=False)
    for material in selected['direct_materials']:
        actual = originals.material_path(child, material)
        assert any(argv[index:index + 3] == ['--ro-bind', str(actual), str(c.workspace(child, 3) / material['path'])]
            for index in range(len(argv) - 2))
    assert str(scratch) not in argv and str(author / 'work') not in argv
    assert not selected.get('execution_qualifications')
    c._validate_inputs(child, c.workspace(child, 3), selected)
