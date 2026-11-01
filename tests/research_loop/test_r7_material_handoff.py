"""Current scientific materials survive direction relays without ancestry growth."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c, experiment_client, history, originals
from test_direct_input_bindings import direct_instance, stopped_seals
from test_history_handoff import finish


@pytest.fixture
def material_scene(tmp_path, monkeypatch):
    root, _ = direct_instance(tmp_path)
    stopped_seals(monkeypatch)
    finish(root, 1, 'H', [], action='experiment')
    finish(root, 2, 'W', [1], files={
        'model.bin': 'one original model', 'train-source.json': '{"source":"original"}'})
    return root


def paths(job):
    return {row['path'] for row in job['direct_materials']}


def relay(root, *, selection=None, report=True):
    finish(root, 3, 'A', [1, 2], action='revise_h',
        files={'assessment.json': '{"gap":"declared"}'} if report else None)
    return finish(root, 4, 'H', [1, 2, 3], action='experiment', selection=selection)


def test_r7_null_direction_relay_keeps_originals_and_exact_implementation_ids(material_scene):
    root = material_scene
    hjob, _, record = relay(root)
    seal = c.read(record / 'result-identity.json')
    assert seal['relayed_materials'] == hjob['direct_materials']
    assert not seal.get('selected_inputs')
    authored = (record / 'science-output.json').read_bytes()
    worker = c.build_job(root, 5, 'W', [4])
    assert paths(worker) == paths(hjob) == {
        'inputs/parents/002/artifacts/model.bin',
        'inputs/parents/002/artifacts/train-source.json',
        'inputs/parents/003/artifacts/assessment.json'}
    options = {row['id']: row['label'] for row in worker['implementation_options']}
    assert options == {'W002-I001': 'model.bin', 'W002-I002': 'train-source.json',
        'A003-I001': 'assessment.json'}
    task = c.read(c.workspace(root, 5) / 'science-task.json')
    assert dict(id='W002-I001', label='model.bin', path='inputs/parents/002/artifacts/model.bin') in task['lineage']['implementation_options']
    from scidiscovery.research_loop.protocol import stage_scope
    config = dict(record_dir=str(experiment_client.record_directory(root, 5)),
        scope=stage_scope(root, worker))
    for object_id in options:
        material = history.resolve_implementation(root, object_id, worker['history']['prefix'])
        assert material in worker['direct_materials']
        expected = originals.material_path(root, material)
        reference = experiment_client.input_reference(config, c.workspace(root, 5) / material['path'])
        assert reference['path'] == str(expected)
        assert not (c.workspace(root, 5) / material['path']).exists()
        assert expected.read_bytes() == Path(material['source']).read_bytes()
    provenance = c.read(root / worker['source_provenance']['path'])
    assert {row['step'] for row in provenance['edges'] if row['kind'] == 'producer'} == {2, 3, 4}
    assert not [row for row in provenance['edges'] if row['kind'] == 'unknown']
    assert (record / 'science-output.json').read_bytes() == authored
    assert c.build_job(root, 5, 'W', [4]) == worker


@pytest.mark.parametrize('selection, expected', [
    ([], set()),
    (['W002-I002'], {'inputs/parents/002/artifacts/train-source.json'}),
])
def test_r7_explicit_clear_or_replace_overrides_the_direction_relay(material_scene, selection, expected):
    root = material_scene
    relay(root, selection=selection)
    worker = c.build_job(root, 5, 'W', [4])
    assert paths(worker) == expected
    assert {row['material']['path'] for row in worker.get('selected_inputs', [])} == expected
    assert {row['id'] for row in worker['implementation_options']} == set(selection)


@pytest.mark.parametrize('selection, expected', [
    ([], set()),
    (['W002-I002'], {'inputs/parents/002/artifacts/train-source.json'}),
])
def test_r7_assessment_clear_or_replace_survives_null_h_with_older_parents(material_scene, selection, expected):
    root = material_scene
    _, _, record = finish(root, 3, 'A', [1, 2], action='revise_h', selection=selection,
        files={'assessment.json': '{"gap":"declared"}'})
    assessed = c.read(record / 'result-identity.json')
    assert {row['path'] for row in assessed['relayed_materials']} == expected
    hjob, _, _ = finish(root, 4, 'H', [1, 2, 3], action='experiment')
    expected = expected | {'inputs/parents/003/artifacts/assessment.json'}
    assert paths(hjob) == expected
    worker = c.build_job(root, 5, 'W', [4])
    assert paths(worker) == expected
    assert {row['id'] for row in worker['implementation_options']} == {'A003-I001', *selection}
    assert len(hjob['direct_refs']) == 3  # Older conclusions remain available as scientific context.


def test_r7_unresolved_assessment_selection_requires_author_repair_without_fallback(material_scene):
    from scidiscovery.research_loop.output_repair import OutputRepairRequired

    root = material_scene
    with pytest.raises(OutputRepairRequired, match='implementation_unresolved') as error:
        finish(root, 3, 'A', [1, 2], action='revise_h', selection=['W999-I001'])
    inventory = error.value.inventory
    assert inventory.selected_inputs == []
    assert inventory.selection_diagnostics == [dict(code='implementation_unresolved', subject='W999-I001')]
    record = c.host_record(c.workspace(root, 3))
    assert c.read(record / 'science-output.json')['implementation_selection'] == ['W999-I001']
    assert not (record / 'result-identity.json').exists()
    assert not (root / 'jobs/004.json').exists()
    assert not c.workspace(root, 4).exists()


@pytest.mark.parametrize('selection, expected', [
    (None, {'inputs/parents/002/artifacts/model.bin',
        'inputs/parents/002/artifacts/train-source.json'}),
    ([], set()),
    (['W002-I002'], {'inputs/parents/002/artifacts/train-source.json'}),
])
def test_r7_assessment_extraction_return_preserves_current_selection(material_scene, selection, expected):
    root = material_scene
    finish(root, 3, 'A', [1, 2], action='extract', selection=selection,
        files={'assessment.json': '{"gap":"declared"}'})
    c.build_job(root, 4, 'E', [1, 2, 3], instruction='Exact scientific question',
        evidence_return=dict(role='A', step=3))
    extraction, _, record = finish(root, 4, 'E', [1, 2, 3],
        files={'evidence.txt': 'An exact requested source excerpt'})
    expected = expected | {'inputs/parents/003/artifacts/assessment.json'}
    assert paths(extraction) == expected
    assert c.read(record / 'result-identity.json')['relayed_materials'] == extraction['direct_materials']
    c.build_job(root, 5, 'A', [1, 2, 3, 4], instruction='Exact scientific question')
    assessment, _, _ = finish(root, 5, 'A', [1, 2, 3, 4], action='continue_w')
    expected = expected | {'inputs/parents/004/artifacts/evidence.txt'}
    assert paths(assessment) == expected
    worker = c.build_job(root, 6, 'W', [5])
    assert paths(worker) == expected
    selected = {'inputs/parents/002/artifacts/train-source.json'} if selection else set()
    assert {row['material']['path'] for row in worker.get('selected_inputs', [])} == selected


def test_r7_h_extraction_return_retains_carried_assessment_reports(material_scene):
    root = material_scene
    finish(root, 3, 'A', [1, 2], action='revise_h',
        files={'assessment.json': '{"gap":"declared"}'})
    direction, _, _ = finish(root, 4, 'H', [1, 2, 3], action='extract')
    c.build_job(root, 5, 'E', [4], instruction='Exact scientific question',
        evidence_return=dict(role='H', step=4))
    finish(root, 5, 'E', [4], files={'evidence.txt': 'A bounded extraction'})
    c.build_job(root, 6, 'H', [4, 5], instruction='Exact scientific question')
    returned, _, _ = finish(root, 6, 'H', [4, 5], action='experiment')
    expected = paths(direction) | {'inputs/parents/005/artifacts/evidence.txt'}
    assert paths(returned) == expected
    assert paths(c.build_job(root, 7, 'W', [6])) == expected


def test_r7_e_without_an_exact_bound_requester_does_not_publish_a_relay(material_scene):
    root = material_scene
    _, _, record = finish(root, 3, 'E', [1, 2])
    assert 'relayed_materials' not in c.read(record / 'result-identity.json')


def test_r7_new_worker_outputs_replace_the_automatic_working_set(material_scene):
    root = material_scene
    relay(root)
    finish(root, 5, 'W', [4], files={'current-model.bin': 'the next original model'})
    assessment, _, record = finish(root, 6, 'A', [4, 5], action='revise_h',
        files={'current-report.json': '{"new":"observation"}'})
    assert paths(assessment) == {'inputs/parents/005/artifacts/current-model.bin'}
    assert not c.read(c.host_record(c.workspace(root, 5)) / 'result-identity.json').get('relayed_materials')
    finish(root, 7, 'H', [4, 5, 6], action='experiment')
    successor = c.build_job(root, 8, 'W', [7])
    assert paths(successor) == {'inputs/parents/005/artifacts/current-model.bin',
        'inputs/parents/006/artifacts/current-report.json'}
    assert {row['id'] for row in successor['implementation_options']} == {'W005-I001', 'A006-I001'}
    assert c.read(record / 'result-identity.json')['relayed_materials'] == assessment['direct_materials']


def test_r7_report_only_worker_can_explicitly_keep_an_original_model(material_scene):
    root = material_scene
    relay(root)
    _, work, _ = finish(root, 5, 'W', [4], selection=['W002-I001'],
        text='[screen report](artifacts/screen-report.json)',
        files={'screen-report.json': '{"fit":"diagnostic"}'})
    next_job = c.build_job(root, 6, 'A', [4, 5])
    assert paths(next_job) == {'inputs/parents/002/artifacts/model.bin',
        'inputs/parents/005/artifacts/screen-report.json'}
    assert next_job['direct_entry_points'] == [dict(label='screen report',
        path='inputs/parents/005/artifacts/screen-report.json')]
    assert next_job['selected_inputs'][0]['material']['source'] == str(c.workspace(root, 2) / 'artifacts/model.bin')
    assert (work / 'artifacts/screen-report.json').read_text() == '{"fit":"diagnostic"}'


def test_r7_only_published_reports_enter_the_relay(material_scene):
    root = material_scene
    author = c.build_job(root, 3, 'A', [1, 2])
    work = c.workspace(root, 3)
    (work / 'work/screen-report.json').write_text('scratch observation')
    (work / 'code').mkdir()
    (work / 'code/analyze.py').write_text('private diagnostic code')
    finish(root, 3, 'A', [1, 2], action='revise_h',
        files={'screen-report.json': 'published observation'})
    finish(root, 4, 'H', [3], action='experiment')
    worker = c.build_job(root, 5, 'W', [4])
    assert paths(worker) == paths(author) | {'inputs/parents/003/artifacts/screen-report.json'}
    assert not any('analyze.py' in row['path'] or '/work/' in row['path'] for row in worker['direct_materials'])


def test_r7_selected_fork_preserves_relay_originals_and_ids_through_next_worker(material_scene):
    from scidiscovery.research_loop import controller, fork_runtime, fork_service

    root = material_scene
    source, _, _ = relay(root)
    for step in (1, 2, 3, 4):
        (c.host_record(c.workspace(root, step)) / 'runner.lock').touch(exist_ok=True)
    with controller._writer(root):
        pass
    child = root.parent / 'fork-child'
    limits = dict(max_tokens=1000, max_seconds=30, max_steps=2)
    preview = fork_service.preview(root, 4, child, budget=limits)
    fork_service.create(root, 4, child, budget=limits,
        expected_preview_id=preview['preview_id'], operation_id='r7-current-material-originals')
    selected = fork_runtime.build_selected_job(child, 4)
    assert selected['direct_materials'] == source['direct_materials']
    assert selected['implementation_options'] == source['implementation_options']
    for material in selected['direct_materials']:
        original = originals.material_path(child, material)
        assert original == Path(material['source'])
        assert original.read_bytes() == Path(material['source']).read_bytes()
    c._validate_inputs(child, c.workspace(child, 4), selected)
    finish(child, 4, 'H', [1, 2, 3], action='experiment')
    completed = c.read(c.host_record(c.workspace(child, 4)) / 'result-identity.json')
    assert completed['relayed_materials'] == selected['direct_materials']
    worker = c.build_job(child, 5, 'W', [4])
    assert worker['direct_materials'] == source['direct_materials']
    assert worker['implementation_options'] == source['implementation_options']
    for option in worker['implementation_options']:
        material = history.resolve_implementation(child, option['id'], worker['history']['prefix'])
        assert material in source['direct_materials']
        assert originals.material_path(child, material) == Path(material['source'])
