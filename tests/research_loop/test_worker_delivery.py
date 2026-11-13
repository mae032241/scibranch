"""Science delivery structure, exact frozen observations and read-only validation."""
import os
from pathlib import Path

import pytest

from scidiscovery.research_loop import (contracts as c, execution, native,
    worker_delivery, checkpoint, continuation, controller, budget, runtime)
from scidiscovery.research_loop.output_repair import OutputRepairRequired,WorkRepairRequired
from execution_fixtures import execution_registry, remote_registry
from test_breadth_science import findings
from test_nonlocal_execution_observation import case, run


def scene(tmp_path,monkeypatch):
    root,job,_,cli=case(tmp_path,monkeypatch)
    return root,job,cli


def output(root,rows,*,intent='decision',raw_text=None):
    record=execution.record(root,2)
    job=c.read(root/'jobs/002.json')
    gaps=[dict(mechanism_ref=row['mechanism_ref'],reason='Fixture has no scientific candidate suitability judgment; retained originals remain available.')
        for row in job.get('candidate_nomination_mechanisms',[]) if row['status']!='waived']
    payload=dict(summary='Independent shallow comparison',scientific_result='These observations retain scientific limitations.',
        limitations=['Mechanical completion alone does not establish screening'],implementation_selection=None,
        mechanism_findings=rows,stage_intent=intent,
        candidate_nominations=dict(representatives=[],extras=[],gaps=gaps) if gaps else None)
    if raw_text is None:
        c.write(record/'science-output.json',payload)
    else:
        (record/'science-output.json').write_text(raw_text)
    c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,native_started=False,usage=native.usage(root),
        raw_ref=c.file_ref(record/'science-output.json')))
    return record,payload


def tree(root):
    """Selected immutable originals, with no recursive workspace inventory."""
    record=root/'native-evidence/002'
    paths=[root/'jobs/002.json',root/'host-history/nodes/002.json',root/'host-history/overview.json']
    paths.extend(record/name for name in ('native-intent.json','native-outcome.json','science-output.json','usage.json',
        'work-evidence.json','result-identity.json','result.json','checkpoint.json','stage-end.json'))
    return {str(path.relative_to(root)):path.read_bytes() for path in paths if path.is_file()}


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault',['unknown_terminal','invalid_json'])
def test_unknown_native_terminal_and_invalid_json_keep_original_unsealed(tmp_path,remote_registry,monkeypatch,fault):
    root,job,config=scene(tmp_path,monkeypatch);run(root,config,'1')
    record,_=output(root,findings('P',blocked=('Q',)),
        raw_text='not JSON' if fault=='invalid_json' else None)
    if fault=='unknown_terminal':
        outcome=c.read(record/'native-outcome.json');outcome['returncode']=None
        c.write(record/'native-outcome.json',outcome)
        with pytest.raises(ValueError):c.seal_result(root,2)
    else:
        with pytest.raises(OutputRepairRequired) as caught:c.seal_result(root,2)
        assert not isinstance(caught.value,WorkRepairRequired)
    assert not (record/'result-identity.json').exists() and not (record/worker_delivery.FACT_NAME).exists()


def stopped_native(root,record):
    """Exact known native originals without an optional node-budget slice."""
    identity=execution.identity(root,2)
    for index,name in enumerate(('runner-process.json','native-process.json')):
        process_id=os.getpid()+10000+index
        c.write(record/name,dict(task_ref=identity,boot_id=budget.clock_stamp()['boot_id'],
            pid=process_id,start_ticks=1,pgid=process_id,sid=process_id,ppid=os.getpid()))
    outcome=c.read(record/'native-outcome.json')
    outcome.update(native_started=True,cleanup='observed_group_cleaned',execution_processes={
        name:c.record_ref(record/name) for name in ('runner-process.json','native-process.json')})
    c.write(record/'native-outcome.json',outcome)


@pytest.mark.process_e2e
def test_direct_seal_never_captures_while_original_writer_is_live(tmp_path,remote_registry,monkeypatch):
    root,job,config=scene(tmp_path,monkeypatch);run(root,config,'1')
    record,_=output(root,findings('P','Q'));stopped_native(root,record)
    monkeypatch.setattr(runtime,'process_matches',lambda *args:True)
    before=tree(root)
    with pytest.raises((ValueError,BlockingIOError),match='Original (native writer|execution bridge) still owns this attempt'):c.seal_result(root,2)
    assert tree(root)==before
    assert not (record/'checkpoint.json').exists() and not (record/worker_delivery.FACT_NAME).exists()


def seal_materials(root,inventory):
    """One filesystem producer validates; the sealing consumer receives its facts."""
    inventory.materials,inventory.mutable_materials=c._material_inventory(root,1,inventory=inventory)
    return c.collect_materials(root,1,inventory=inventory)


@pytest.fixture
def stopped_deliverables(tmp_path,monkeypatch):
    root=tmp_path/'instance';(root/'jobs').mkdir(parents=True)
    c.write(root/'jobs/001.json',dict(role='H',step=1))
    work=root/'project/nodes/001';(work/'artifacts').mkdir(parents=True)
    record=c.host_record(work);record.mkdir(parents=True)
    policy={}
    monkeypatch.setattr(c,'settings',lambda *args:policy)
    monkeypatch.setattr(c,'workspace',lambda *args:work)
    monkeypatch.setattr(execution,'record',lambda *args:record)
    monkeypatch.setattr(worker_delivery,'writers_stopped',lambda *args:None)
    first=work/'artifacts/a-first.bin';first.write_bytes(b'first');first.chmod(0o640)
    inventory=c.CurrentInventory(root=root,job=dict(role='H',step=1),record=record,attempt=0,
        managed_calls=[],scientific_outputs={},policy=policy)
    return root,work,first,policy,inventory


@pytest.mark.parametrize('fault',['symlink','hardlink','nested_experiment'])
def test_collect_materials_rejects_later_invalid_inventory_before_chmod(stopped_deliverables,fault):
    root,work,first,policy,inventory=stopped_deliverables
    later=work/'artifacts/z-later.bin'
    if fault=='symlink':later.symlink_to(first)
    elif fault=='hardlink':
        later.write_bytes(b'later output');later.chmod(0o640)
        os.link(later,root/'later-alias')
    elif fault=='nested_experiment':(work/'experiments').symlink_to(work/'artifacts',target_is_directory=True)
    before=first.stat().st_mode
    with pytest.raises(ValueError):seal_materials(root,inventory)
    assert first.stat().st_mode==before and first.read_bytes()==b'first'


def test_collect_materials_seals_original_paths_after_complete_inventory(stopped_deliverables):
    root,work,first,policy,inventory=stopped_deliverables
    conclusion=work/'experiments/one/conclusion.md';conclusion.parent.mkdir(parents=True)
    conclusion.write_bytes(b'conclusion');conclusion.chmod(0o640)
    identity={path:(path.stat().st_dev,path.stat().st_ino) for path in (first,conclusion)}
    rows=seal_materials(root,inventory)
    assert {row['path'] for row in rows}=={'inputs/parents/001/artifacts/a-first.bin',
        'inputs/parents/001/artifacts/experiments/one/conclusion.md'}
    assert {row['source'] for row in rows}=={str(first),str(conclusion)}
    for path,original in identity.items():
        assert path.stat().st_mode&0o777==0o400
        assert (path.stat().st_dev,path.stat().st_ino)==original


@pytest.mark.parametrize('managed_kind',['frozen_alias','collision','unavailable'])
def test_collect_materials_validates_managed_outputs_before_mutable_seal(stopped_deliverables,monkeypatch,managed_kind):
    from scidiscovery.research_loop import history
    root,work,first,policy,inventory=stopped_deliverables
    inventory.job['role']='W'
    c.write(root/'jobs/001.json',inventory.job)
    managed=root/'retained.bin';managed.write_bytes(b'retained');managed.chmod(0o400)
    os.link(managed,root/'retained-alias')
    monkeypatch.setattr(history,'managed_calls',lambda *args:[dict(call_id='call',status='known_success')])
    monkeypatch.setattr(history,'scientific_outputs',lambda *args:[dict(path='retained.bin',name='data.bin',
        retention='unavailable' if managed_kind=='unavailable' else 'archived')])
    if managed_kind=='collision':
        duplicate=work/'artifacts/experiments/call/artifacts/data.bin'
        duplicate.parent.mkdir(parents=True);duplicate.write_bytes(b'duplicate');duplicate.chmod(0o640)
    before=first.stat().st_mode
    if managed_kind=='frozen_alias':
        rows=seal_materials(root,inventory)
        assert first.stat().st_mode&0o777==0o400
        assert any(row['source']==str(managed) for row in rows)
        assert managed.samefile(root/'retained-alias')
    else:
        with pytest.raises((ValueError,OutputRepairRequired),match='colli|unavailable|publication'):seal_materials(root,inventory)
        assert first.stat().st_mode==before
        if managed_kind=='collision':assert duplicate.stat().st_mode&0o777==0o640
    assert managed.stat().st_mode&0o777==0o400 and managed.read_bytes()==b'retained'
