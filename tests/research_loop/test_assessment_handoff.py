"""Exact ordinary assessment context and reusable declared A originals."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c, controller, history, originals, shared_input_views
from test_direct_input_bindings import direct_instance, stopped_seals
from test_history_handoff import finish


@pytest.fixture
def scene(tmp_path, monkeypatch):
    root, _ = direct_instance(tmp_path)
    stopped_seals(monkeypatch)
    finish(root, 1, 'H', [], action='experiment')
    finish(root, 2, 'W', [1])
    return root


def decision(root, action='continue_w', files=None):
    return finish(root, 3, 'A', [1, 2], action=action, files=files)


def worker(root, step, parents):
    c.build_job(root, step, 'W', parents, instruction='Exact scientific question')
    return finish(root, step, 'W', parents)


def assert_original(root, job, step):
    work = c.workspace(root, job['step'])
    handoff = job['assessment_handoff']
    prior = handoff['previous_a']
    assert prior['step'] == step
    bound = next(row for row in job['history']['files'] if row['path'] == prior['original'])
    actual = originals.resolve_root_ref(root, dict(bound, path=str(work / prior['original'])))
    expected = c._execution_raw(root, step)
    assert actual == expected
    argv = shared_input_views.mount_arguments(root, job, work, include_skills=False)
    assert any(argv[index:index + 3] == ['--ro-bind', str(expected), str(work / prior['original'])]
               for index in range(len(argv) - 2))
    assert not (work / prior['original']).exists()
    assert c.read(work / 'science-task.json')['assessment_handoff'] == handoff


def test_continue_w_binds_exact_previous_assessment_original(scene):
    first, _, _ = decision(scene)
    assert first['assessment_handoff'] == dict(current_w='inputs/parents/002/conclusion.json',
        current_h='inputs/parents/001/conclusion.json')
    worker(scene, 4, [1, 2, 3])
    job = c.build_job(scene, 5, 'A', [1, 4])
    assert job['assessment_handoff']['current_w'] == 'inputs/parents/004/conclusion.json'
    assert job['assessment_handoff']['previous_a']['relation'] == 'context_only'
    assert [ref['path'] for ref in job['direct_refs']] == [
        'inputs/parents/001/conclusion.json', 'inputs/parents/004/conclusion.json']
    assert_original(scene, job, 3)
    assert not job.get('execution_qualifications')


def test_revised_h_keeps_prior_a_as_scope_revision_context(scene):
    decision(scene, 'revise_h')
    c.build_job(scene, 4, 'H', [1, 2, 3], instruction='Exact scientific question')
    finish(scene, 4, 'H', [1, 2, 3], action='experiment')
    worker(scene, 5, [4])
    job = c.build_job(scene, 6, 'A', [4, 5])
    assert job['assessment_handoff']['current_h'] == 'inputs/parents/004/conclusion.json'
    assert job['assessment_handoff']['previous_a']['relation'] == 'scope_revision_context'
    assert_original(scene, job, 3)


def test_unrelated_a_does_not_become_the_workers_previous_assessment(scene):
    decision(scene, 'wait', files={'unrelated.py': 'Not bound to this W decision'})
    # This exact W/H binding never received A003's decision.
    finish(scene, 4, 'W', [1])
    job = c.build_job(scene, 5, 'A', [1, 4])
    assert 'previous_a' not in job['assessment_handoff']
    assert not job['direct_materials']


def test_a_declared_artifact_can_be_selected_but_work_scratch_cannot(scene):
    _, author, _ = decision(scene, files={'check.py': 'print(42)\n', 'report.txt': 'A declared check result'})
    scratch = author / 'work/private-check.py'
    scratch.write_text('This scratch file is not a retained scientific original')
    worker(scene, 4, [1, 2, 3])
    job = c.build_job(scene, 5, 'A', [1, 4])
    prior = job['assessment_handoff']['previous_a']
    assert prior['relation'] == 'context_only'
    assert set(prior['materials']) == {'inputs/parents/003/artifacts/check.py',
        'inputs/parents/003/artifacts/report.txt'}
    assert set(prior['materials']) == {row['path'] for row in job['direct_materials']}
    argv = shared_input_views.mount_arguments(scene, job, c.workspace(scene, 5), include_skills=False)
    for material in job['direct_materials']:
        original = originals.material_path(scene, material)
        assert any(argv[index:index + 3] == ['--ro-bind', str(original), str(c.workspace(scene, 5) / material['path'])]
                   for index in range(len(argv) - 2))
        assert original == author / 'artifacts' / Path(material['path']).name
        assert original.read_text() == {'check.py': 'print(42)\n',
            'report.txt': 'A declared check result'}[original.name]
        assert original.stat().st_mode & 0o777 == 0o400
    assert str(scratch) not in argv and str(author / 'work') not in argv
    assert not job.get('execution_qualifications')
    frozen = (scene / 'jobs/005.json').read_bytes()
    assert c.build_job(scene, 5, 'A', [1, 4]) == job
    assert (scene / 'jobs/005.json').read_bytes() == frozen
    index = c.read(c.workspace(scene, 5) / prior['implementations'])
    assert [row['id'] for row in index['implementations']] == ['A003-I001', 'A003-I002']
    selected = c.selected_inputs(c.workspace(scene, 5), dict(implementation_selection=['A003-I001']), job)
    assert len(selected) == 1
    assert originals.material_path(scene, selected[0]['material']) == author / 'artifacts/check.py'
    assert 'private-check.py' not in str(index)
    assert history.resolve_implementation(scene, 'A003-I001', job['history']['nodes']) == selected[0]['material']
    finish(scene, 5, 'A', [1, 4], action='continue_w', selection=['A003-I001'])
    successor = c.build_job(scene, 6, 'W', [1, 4, 5], instruction='Exact scientific question')
    assert [row['path'] for row in successor['direct_materials']] == ['inputs/parents/003/artifacts/check.py']
    argv = shared_input_views.mount_arguments(scene, successor, c.workspace(scene, 6), include_skills=False)
    assert str(author / 'artifacts/check.py') in argv
    assert str(scratch) not in argv and str(author / 'work') not in argv


def test_exact_extraction_return_uses_its_requesting_a(scene):
    decision(scene, 'extract')
    c.build_job(scene, 4, 'E', [1, 2, 3], instruction='Exact scientific question',
        evidence_return=dict(role='A', step=3))
    finish(scene, 4, 'E', [1, 2, 3])
    job = c.build_job(scene, 5, 'A', [1, 2, 3, 4], instruction='Exact scientific question')
    assert_original(scene, job, 3)


def test_exact_audit_return_uses_its_direct_a_parent(scene):
    decision(scene, 'audit')
    job = c.build_job(scene, 4, 'A', [1, 2, 3], instruction='Exact scientific question')
    assert_original(scene, job, 3)


def test_frozen_preexisting_job_without_handoff_stays_unchanged(scene):
    decision(scene)
    worker(scene, 4, [1, 2, 3])
    job = c.build_job(scene, 5, 'A', [1, 4])
    work = c.workspace(scene, 5)
    job.pop('assessment_handoff')
    view = c.read(work / 'science-task.json')
    view.pop('assessment_handoff')
    c.write(scene / 'jobs/005.json', job)
    c.write(work / 'science-task.json', view)
    frozen = (scene / 'jobs/005.json').read_bytes(), (work / 'science-task.json').read_bytes()
    assert c.build_job(scene, 5, 'A', [1, 4]) == job
    assert frozen == ((scene / 'jobs/005.json').read_bytes(), (work / 'science-task.json').read_bytes())


@pytest.mark.parametrize('field,value', [
    ('purpose', 'closeout'), ('final_report', {'source_cutoff': 2}), ('final_only', True),
    ('formal_review', {'subject': 2}), ('author_correction', {'root': 3}),
    ('correction_request', {'source': 3}), ('fork_source', {'step': 2}),
])
def test_special_assessments_keep_their_original_contract(scene, field, value):
    job = c.build_job(scene, 3, 'A', [1, 2])
    assert c._assessment_handoff(scene, dict(job, **{field: value})) is None


def test_changed_assessment_source_and_public_view_are_rejected(scene):
    decision(scene)
    worker(scene, 4, [1, 2, 3])
    job = c.build_job(scene, 5, 'A', [1, 4])
    work = c.workspace(scene, 5)
    changed = c.read(work / 'science-task.json')
    changed['assessment_handoff']['previous_a']['original'] = 'inputs/history/nodes/002/scientific-original.json'
    c.write(work / 'science-task.json', changed)
    with pytest.raises(ValueError, match='assessment sources'):
        c._validate_task_view(scene, work, job)
    job['assessment_handoff']['previous_a']['step'] = 2
    c.write(work / 'science-task.json', c.science_view(job, scene))
    with pytest.raises(ValueError, match='decision lineage'):
        c._validate_inputs(scene, work, job)


def test_changed_prior_a_declared_material_is_rejected(scene):
    _, author, _ = decision(scene, files={'check.py': 'print(42)\n'})
    worker(scene, 4, [1, 2, 3])
    job = c.build_job(scene, 5, 'A', [1, 4])
    original = author / 'artifacts/check.py'
    original.chmod(0o600)
    original.write_text('print(43)\n')
    with pytest.raises(ValueError, match='changed|identity|original'):
        shared_input_views.mount_arguments(scene, job, c.workspace(scene, 5), include_skills=False)


def test_prior_a_materials_share_the_existing_combined_input_bound(tmp_path, monkeypatch):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Compare bounded declared originals')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fake'], max_tokens=10000, max_seconds=300,
        max_steps=8, model='fixture', reasoning_effort='high', search_policy=None,
        experiment_mode='external', max_output_item_bytes=64, max_output_total_bytes=96)
    stopped_seals(monkeypatch)
    finish(root, 1, 'H', [], action='experiment')
    finish(root, 2, 'W', [1])
    decision(root, files={'check.py': 'x' * 63, 'small-report.txt': 'x'})
    c.build_job(root, 4, 'W', [1, 2, 3], instruction='Exact scientific question')
    finish(root, 4, 'W', [1, 2, 3], files={'observation.txt': 'y' * 64})
    job = c.build_job(root, 5, 'A', [1, 4])
    prior = job['assessment_handoff']['previous_a']
    assert prior['materials'] == []
    assert prior['materials_unavailable_reason'] == 'input_material_bound'
    assert [row['path'] for row in job['direct_materials']] == ['inputs/parents/004/artifacts/observation.txt']
    assert_original(root, job, 3)
    argv = shared_input_views.mount_arguments(root, job, c.workspace(root, 5), include_skills=False)
    required = c.workspace(root, 4) / 'artifacts/observation.txt'
    optional = c.workspace(root, 3) / 'artifacts/check.py'
    assert any(argv[index:index + 3] == ['--ro-bind', str(required),
        str(c.workspace(root, 5) / 'inputs/parents/004/artifacts/observation.txt')]
        for index in range(len(argv) - 2))
    assert str(optional) not in argv
    assert str(c.workspace(root, 3) / 'artifacts/small-report.txt') not in argv
    c._validate_inputs(root, c.workspace(root, 5), job)
    finish(root, 5, 'A', [1, 4], action='wait')


def test_selected_fork_retains_prior_a_declared_originals(scene):
    from scidiscovery.research_loop import fork_runtime, fork_service

    _, author, _ = decision(scene, files={'check.py': 'print(42)\n', 'report.txt': 'Retained check'})
    scratch = author / 'work/private-check.py'
    scratch.write_text('Scratch is not an input to the future assessment')
    worker(scene, 4, [1, 2, 3])
    source_job, _, _ = finish(scene, 5, 'A', [1, 4], action='wait')
    # These thin fixture completions have no real runner; retain the same
    # quiescent lock markers used by existing fork consumer fixtures.
    for step in (1, 2, 3, 4, 5):
        (c.host_record(c.workspace(scene, step)) / 'runner.lock').touch(exist_ok=True)
    with controller._writer(scene):
        pass
    child = scene.parent / 'fork-child'
    limits = dict(max_tokens=1000, max_seconds=30, max_steps=1)
    preview = fork_service.preview(scene, 5, child, budget=limits)
    fork_service.create(scene, 5, child, budget=limits, expected_preview_id=preview['preview_id'],
        operation_id='assessment-declared-originals')
    selected = fork_runtime.build_selected_job(child, 5)
    assert selected['assessment_handoff'] == source_job['assessment_handoff']
    assert selected['direct_materials'] == source_job['direct_materials']
    argv = shared_input_views.mount_arguments(child, selected, c.workspace(child, 5), include_skills=False)
    for material in selected['direct_materials']:
        actual = originals.material_path(child, material)
        assert actual == author / 'artifacts' / Path(material['path']).name
        assert any(argv[index:index + 3] == ['--ro-bind', str(actual), str(c.workspace(child, 5) / material['path'])]
                   for index in range(len(argv) - 2))
    assert str(scratch) not in argv and str(author / 'work') not in argv
    assert not selected.get('execution_qualifications')
    c._validate_inputs(child, c.workspace(child, 5), selected)
