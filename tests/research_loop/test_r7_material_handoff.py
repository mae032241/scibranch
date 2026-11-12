"""Exact current materials survive W-led consultations without ancestry growth."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c, experiment_client, handoff_state, history, originals
from test_direct_input_bindings import direct_instance, stopped_seals
from test_history_handoff import finish


@pytest.fixture
def material_scene(tmp_path, monkeypatch):
    def scene(role='H'):
        root, _ = direct_instance(tmp_path)
        stopped_seals(monkeypatch)
        finish(root, 1, 'H', [], action='experiment')
        finish(root, 2, 'W', [1], stage_intent={'H':'revise_h','A':'review','E':'extract'}[role],
            files={'model.bin':'one original model', 'train-source.json':'{"source":"original"}'})
        return root
    return scene


def paths(job):
    return {row['path'] for row in job['direct_materials']}


def relay_materials(root, seal):
    return handoff_state.relayed_materials(root, seal, sources={})


def consult(root, role='H', *, selection=None, report=True):
    c.build_job(root,3,role,[1,2],instruction='Exact scientific question',
        evidence_return=dict(role='W',step=2))
    return finish(root,3,role,[1,2],selection=selection,
        action='continue_w' if role=='A' else 'wait',
        files={'report.json':'{"gap":"declared"}'} if report else None)


@pytest.mark.parametrize('role',['H','A','E'])
def test_r7_consultation_relay_keeps_originals_and_exact_implementation_ids(material_scene,role):
    root=material_scene(role)
    consultation,_,record=consult(root,role)
    seal=c.read(record/'result-identity.json')
    assert relay_materials(root,seal)==consultation['direct_materials']
    assert not seal.get('selected_inputs')
    authored=(record/'science-output.json').read_bytes()
    worker=c.build_job(root,4,'W',[2,3])
    assert paths(worker)=={
        'inputs/parents/002/artifacts/model.bin',
        'inputs/parents/002/artifacts/train-source.json',
        'inputs/parents/003/artifacts/report.json'}
    options={row['id']:row['label'] for row in worker['implementation_options']}
    assert options=={'W002-I001':'model.bin','W002-I002':'train-source.json',f'{role}003-I001':'report.json'}
    task=c.read(c.workspace(root,4)/'science-task.json')
    assert dict(id='W002-I001',label='model.bin',path='inputs/parents/002/artifacts/model.bin') in task['lineage']['implementation_options']
    from scidiscovery.research_loop.protocol import stage_scope
    config=dict(record_dir=str(experiment_client.record_directory(root,4)),scope=stage_scope(root,worker))
    for object_id in options:
        material=history.resolve_implementation(root,object_id,worker['history']['prefix'])
        assert material in worker['direct_materials']
        expected=originals.material_path(root,material)
        reference=experiment_client.input_reference(config,c.workspace(root,4)/material['path'])
        assert reference['path']==str(expected)
        assert not (c.workspace(root,4)/material['path']).exists()
        assert expected.read_bytes()==Path(material['source']).read_bytes()
    provenance=c.read(root/worker['source_provenance']['path'])
    assert {row['step'] for row in provenance['edges'] if row['kind']=='producer'}=={2,3}
    assert not [row for row in provenance['edges'] if row['kind']=='unknown']
    assert (record/'science-output.json').read_bytes()==authored
    assert c.build_job(root,4,'W',[2,3])==worker


@pytest.mark.parametrize('selection,expected',[
    ([],set()),(['W002-I002'],{'inputs/parents/002/artifacts/train-source.json'}),
])
def test_r7_worker_clear_or_replace_overrides_the_relay(material_scene,selection,expected):
    root=material_scene()
    consult(root,report=False)
    finish(root,4,'W',[2,3],selection=selection)
    worker=c.build_job(root,5,'A',[4])
    assert paths(worker)==expected
    assert {row['material']['path'] for row in worker.get('selected_inputs',[])}==expected
    assert {row['id'] for row in worker['implementation_options']}==set(selection)


@pytest.mark.parametrize('selection,expected',[
    ([],set()),(['W002-I002'],{'inputs/parents/002/artifacts/train-source.json'}),
])
def test_r7_advisory_selection_cannot_override_worker_selection(material_scene,selection,expected):
    root=material_scene('A')
    consult(root,'A')
    finish(root,4,'W',[2,3],selection=selection,stage_intent='review')
    c.build_job(root,5,'A',[4],instruction='Exact scientific question',evidence_return=dict(role='W',step=4))
    assessment,_,record=finish(root,5,'A',[4],selection=['W002-I001'],files={'report.json':'New fallible advice'})
    assert {row['path'] for row in relay_materials(root,c.read(record/'result-identity.json'))}==expected
    worker=c.build_job(root,6,'W',[1,4,5])
    assert paths(worker)==expected|{'inputs/parents/005/artifacts/report.json'}
    assert {row['id'] for row in worker['implementation_options']}=={'A005-I001',*selection}
    assert len(assessment['direct_refs'])==1
    assert len(worker['direct_refs'])==3  # Older conclusions remain scientific context.


def test_r7_unresolved_selection_requires_author_repair_without_fallback(material_scene):
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    root=material_scene('A')
    consult(root,'A')
    with pytest.raises(OutputRepairRequired,match='implementation_selection') as error:
        finish(root,4,'W',[2,3],selection=['W999-I001'])
    assert error.value.code=='implementation_unresolved'
    assert error.value.inventory.selected_inputs==[]
    assert error.value.inventory.selection_diagnostics==[dict(code='implementation_unresolved',subject='W999-I001')]
    record=c.host_record(c.workspace(root,4))
    assert c.read(record/'science-output.json')['implementation_selection']==['W999-I001']
    assert not (record/'result-identity.json').exists()
    assert not (root/'jobs/005.json').exists() and not c.workspace(root,5).exists()


def test_r7_e_without_an_exact_bound_requester_does_not_publish_a_relay(material_scene):
    root=material_scene()
    _,_,record=finish(root,3,'E',[1,2])
    assert 'relayed_material_refs' not in c.read(record/'result-identity.json')


def test_r7_worker_explicitly_clears_carried_materials_before_publishing_new_outputs(material_scene):
    root=material_scene()
    consult(root)
    finish(root,4,'W',[2,3],selection=[],files={'current-model.bin':'the next original model'})
    assessment,_,record=finish(root,5,'A',[4],files={'current-report.json':'{"new":"observation"}'})
    assert paths(assessment)=={'inputs/parents/004/artifacts/current-model.bin'}
    assert not c.read(c.host_record(c.workspace(root,4))/'result-identity.json').get('relayed_material_refs')
    successor=c.build_job(root,6,'W',[3,4,5])
    assert paths(successor)=={'inputs/parents/004/artifacts/current-model.bin','inputs/parents/005/artifacts/current-report.json'}
    assert {row['id'] for row in successor['implementation_options']}=={'W004-I001','A005-I001'}
    assert relay_materials(root,c.read(record/'result-identity.json'))==assessment['direct_materials']


def test_r7_report_only_worker_can_explicitly_keep_an_original_model(material_scene):
    root=material_scene()
    consult(root)
    _,work,_=finish(root,4,'W',[2,3],selection=['W002-I001'],
        text='[screen report](artifacts/screen-report.json)',files={'screen-report.json':'{"fit":"diagnostic"}'})
    next_job=c.build_job(root,5,'A',[3,4])
    assert paths(next_job)=={'inputs/parents/002/artifacts/model.bin','inputs/parents/004/artifacts/screen-report.json'}
    assert next_job['direct_entry_points']==[dict(label='screen report',path='inputs/parents/004/artifacts/screen-report.json')]
    assert next_job['selected_inputs'][0]['material']['source']==str(c.workspace(root,2)/'artifacts/model.bin')
    assert (work/'artifacts/screen-report.json').read_text()=='{"fit":"diagnostic"}'


def test_r7_only_published_reports_enter_the_relay(material_scene):
    root=material_scene()
    author=c.build_job(root,3,'H',[1,2],instruction='Exact scientific question',evidence_return=dict(role='W',step=2))
    work=c.workspace(root,3)
    (work/'work/screen-report.json').write_text('scratch observation')
    (work/'code').mkdir();(work/'code/analyze.py').write_text('private diagnostic code')
    consult(root)
    worker=c.build_job(root,4,'W',[2,3])
    assert paths(worker)==paths(author)|{'inputs/parents/003/artifacts/report.json'}
    assert not any('analyze.py' in row['path'] or '/work/' in row['path'] for row in worker['direct_materials'])


def test_r7_selected_fork_preserves_relay_originals_and_ids_through_next_worker(material_scene):
    from scidiscovery.research_loop import controller, fork_runtime, fork_service
    root=material_scene()
    source,_,_=consult(root)
    for step in (1,2,3):
        (c.host_record(c.workspace(root,step))/'runner.lock').touch(exist_ok=True)
    with controller._writer(root):
        pass
    child=root.parent/'fork-child'
    limits=dict(max_tokens=1000,max_seconds=30,max_steps=2)
    preview=fork_service.preview(root,3,child,budget=limits)
    fork_service.create(root,3,child,budget=limits,expected_preview_id=preview['preview_id'],operation_id='r7-current-material-originals')
    selected=fork_runtime.build_selected_job(child,3)
    assert selected['direct_materials']==source['direct_materials']
    assert selected['implementation_options']==source['implementation_options']
    for material in selected['direct_materials']:
        original=originals.material_path(child,material)
        assert original==Path(material['source'])
        assert original.read_bytes()==Path(material['source']).read_bytes()
    c._validate_inputs(child,c.workspace(child,3),selected)
    finish(child,3,'H',[1,2],action='wait')
    completed=c.read(c.host_record(c.workspace(child,3))/'result-identity.json')
    assert relay_materials(child,completed)==selected['direct_materials']
    worker=c.build_job(child,4,'W',[2,3])
    assert worker['direct_materials']==source['direct_materials']
    assert worker['implementation_options']==source['implementation_options']
    for option in worker['implementation_options']:
        material=history.resolve_implementation(child,option['id'],worker['history']['prefix'])
        assert material in source['direct_materials']
        assert originals.material_path(child,material)==Path(material['source'])
