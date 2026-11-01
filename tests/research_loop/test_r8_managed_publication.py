"""Managed call identities own their publication directories; no model calls."""
from copy import deepcopy

import pytest

from scidiscovery.research_loop import contracts as c
from scidiscovery.research_loop.output_repair import OutputRepairRequired


@pytest.fixture
def managed_scene(tmp_path, monkeypatch):
    root=tmp_path/'instance';work=root/'node';record=root/'native-evidence/002'
    work.mkdir(parents=True);record.mkdir(parents=True)
    job=dict(role='W',step=2,handoff_protocol=c.HANDOFF_PROTOCOL,direct_materials=[])
    original=work/'work/retained-experiments/call-1/outputs/model.bin'
    original.parent.mkdir(parents=True);original.write_bytes(b'original model');original.chmod(0o400)
    calls=[dict(call_id='call-1',status='known_success')]
    outputs={'call-1':[dict(name='model.bin',path=original.relative_to(root).as_posix(),retention='archived')]}
    inventory=c.CurrentInventory(root,job,record,0,calls,outputs,policy={})
    monkeypatch.setattr(c,'workspace',lambda *args:work)
    monkeypatch.setattr(c,'host_record',lambda *args:record)
    return root,work,original,inventory


def inventory_rows(scene):
    root,_,_,inventory=scene
    return c._material_inventory(root,2,inventory=inventory)


@pytest.mark.parametrize('owned_path',[
    'artifacts/experiments/call-1/model.bin',
    'artifacts/experiments/call-1/artifacts/model.bin',
    'experiments/call-1/artifacts/model.bin',
])
def test_r8_repackaged_managed_call_requires_output_only_layout_correction(managed_scene,owned_path):
    root,work,original,inventory=managed_scene
    copied=work/owned_path
    copied.parent.mkdir(parents=True);copied.write_bytes(original.read_bytes())
    assert not copied.samefile(original)
    before={path:(path.stat().st_mode,path.read_bytes()) for path in (original,copied)}
    calls=deepcopy(inventory.managed_calls);outputs=deepcopy(inventory.scientific_outputs)
    with pytest.raises(OutputRepairRequired) as caught:inventory_rows(managed_scene)
    request=caught.value.request()
    assert request['code']=='deliverable_path_collision'
    assert owned_path in request['diagnostic'] and str(root) not in request['diagnostic']
    assert 'no rerun/resubmit' in request['diagnostic']
    assert {path:(path.stat().st_mode,path.read_bytes()) for path in before}==before
    assert inventory.managed_calls==calls and inventory.scientific_outputs==outputs

    copied.unlink()  # Correct this completed contribution without another execution.
    rows,mutable=inventory_rows(managed_scene)
    assert mutable==[]
    assert rows==[dict(source=str(original),
        path='inputs/parents/002/artifacts/experiments/call-1/artifacts/model.bin',
        size_bytes=len(b'original model'))]


def test_r8_distinct_new_report_moves_outside_managed_namespace(managed_scene):
    _,work,original,inventory=managed_scene
    report=work/'artifacts/experiments/call-1/screen.json'
    report.parent.mkdir(parents=True);report.write_bytes(b'new authored report, distinct from the retained model')
    # A reserved author layout remains correctable even when the copy exceeds
    # an item or aggregate publication budget. Managed originals still validate.
    inventory.policy=dict(max_output_item_bytes=32,max_output_total_bytes=32)
    with pytest.raises(OutputRepairRequired) as caught:inventory_rows(managed_scene)
    assert caught.value.code=='deliverable_path_collision'
    assert 'move distinct authored reports outside them' in caught.value.diagnostic
    assert report.stat().st_mode & 0o777==0o644
    report.unlink()
    moved=work/'artifacts/analysis/screen.json';moved.parent.mkdir(parents=True)
    moved.write_bytes(b'new report')
    rows,mutable=inventory_rows(managed_scene)
    assert {row['source'] for row in rows}=={str(original),str(moved)}
    assert mutable==[moved]
    assert all(set(row)=={'source','path','size_bytes'} for row in rows)


@pytest.mark.parametrize('call_id',['unrecorded-call','call-1-extra'])
def test_r8_unknown_exact_call_namespaces_are_preserved(managed_scene,call_id):
    _,work,original,_=managed_scene
    authored=work/'artifacts/experiments'/call_id/'future-data.bin'
    authored.parent.mkdir(parents=True);authored.write_bytes(b'new experimental data')
    rows,mutable=inventory_rows(managed_scene)
    assert {row['source'] for row in rows}=={str(original),str(authored)}
    assert mutable==[authored]


def test_r8_accepted_unknown_call_is_not_reinterpreted_or_resubmitted(managed_scene):
    _,work,original,inventory=managed_scene
    inventory.managed_calls[0]['status']='unknown'
    inventory.scientific_outputs.clear()
    authored=work/'artifacts/experiments/call-1/report.json'
    authored.parent.mkdir(parents=True);authored.write_text('author report')
    calls=deepcopy(inventory.managed_calls)
    before=(original.stat().st_mode,original.read_bytes())
    with pytest.raises(OutputRepairRequired):inventory_rows(managed_scene)
    assert inventory.managed_calls==calls
    assert (original.stat().st_mode,original.read_bytes())==before
    authored.unlink()
    assert inventory_rows(managed_scene)==([],[])
    assert inventory.managed_calls==calls


def test_r8_original_integrity_failure_precedes_author_layout_repair(managed_scene):
    _,work,original,_=managed_scene
    authored=work/'artifacts/experiments/call-1/model.bin'
    authored.parent.mkdir(parents=True);authored.write_bytes(original.read_bytes())
    original.chmod(0o600)
    with pytest.raises(ValueError,match='Managed output has not been sealed') as caught:
        inventory_rows(managed_scene)
    assert not isinstance(caught.value,OutputRepairRequired)
    assert authored.stat().st_mode & 0o777==0o644


@pytest.mark.parametrize('operation',['code_selection','payload_path'])
def test_r8_client_operation_loads_each_material_owner_once(tmp_path,monkeypatch,operation):
    from collections import Counter
    from scidiscovery.research_loop import experiment_client, originals
    from scidiscovery.research_loop.protocol import stage_scope
    from test_direct_input_bindings import direct_instance, stopped_seals
    from test_history_handoff import finish

    root,_=direct_instance(tmp_path)
    stopped_seals(monkeypatch)
    finish(root,1,'H',[],action='experiment')
    finish(root,2,'W',[1],files={'model.bin':'one exact original model'})
    finish(root,3,'A',[1,2],action='revise_h',files={'report.json':'one independent report'})
    finish(root,4,'H',[1,2,3],action='experiment')
    job=c.build_job(root,5,'W',[4]);work=c.workspace(root,5)
    config=dict(record_dir=str(root/'host-runtime/experiment-control/005/records'),
        scope=stage_scope(root,job),worker_view_version=1)
    material=next(row for row in job['direct_materials'] if row['path'].endswith('/model.bin'))
    original=originals.material_path(root,material)
    source=work/'code/probe.py';source.parent.mkdir(exist_ok=True);source.write_text('print("bounded fixture")\n')
    owners={root/f'native-evidence/{step:03d}/result-identity.json' for step in (2,3)}
    counts=Counter();read=c.read
    def counted(path):
        if path in owners:counts[path]+=1
        return read(path)
    monkeypatch.setattr(c,'read',counted)
    monkeypatch.setattr(originals,'material_path',lambda *args,**kwargs:pytest.fail('client reopened a loaded producer seal'))

    if operation=='code_selection':
        selected=experiment_client.code_selection(config,source,resolved=source)
        assert selected['code_root']==source.parent and selected['entry']==source.name
    else:
        selected=experiment_client.payload_path(config,work/material['path'])
        assert selected==original
    assert counts==Counter({owner:1 for owner in owners})
