"""Science delivery structure, exact frozen observations and read-only validation."""
import os

import pytest

from scidiscovery.research_loop import (contracts as c, execution, native,
    worker_delivery, checkpoint, continuation, controller, budget, runtime)
from scidiscovery.research_loop.output_repair import OutputRepairRequired,WorkRepairRequired
from execution_fixtures import execution_registry
from test_breadth_science import prepare,h,worker,run,findings

pytestmark = pytest.mark.process_e2e


def scene(tmp_path):
    root=prepare(tmp_path);h(root,labels=('P','Q'))
    job,config=worker(root,tmp_path,2,[1])
    return root,job,config


def output(root,rows,*,intent='review'):
    record=execution.record(root,2)
    job=c.read(root/'jobs/002.json')
    gaps=[dict(mechanism_ref=row['mechanism_ref'],reason='Fixture has no scientific candidate suitability judgment; retained originals remain available.')
        for row in job.get('candidate_nomination_mechanisms',[]) if row['status']!='waived']
    payload=dict(summary='Independent shallow comparison',scientific_result='These observations retain scientific limitations.',
        limitations=['Mechanical completion alone does not establish screening'],implementation_selection=None,
        mechanism_findings=rows,stage_intent=intent,
        candidate_nominations=dict(representatives=[],extras=[],gaps=gaps) if gaps else None)
    c.write(record/'science-output.json',payload)
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


def test_missing_actual_observation_requires_continuation_without_finding_table(tmp_path,execution_registry):
    root,job,config=scene(tmp_path);run(root,config,'P')
    record,payload=output(root,None)
    with pytest.raises(WorkRepairRequired,match='h001.m2'):c.seal_result(root,2)
    assert not (record/'result-identity.json').exists()
    assert worker_delivery.assessment(root,job,payload,record)['status']=='incomplete'
    checkpoint.end_stage(root,job,record,'worker_resume_node_exhausted')
    before=tree(root)
    with pytest.raises(WorkRepairRequired):c.seal_result(root,2)
    assert tree(root)==before
    assert len(execution.records(root,2))==1 and len(config['breadth']['mechanisms'])==2


def test_later_same_owner_call_cannot_wash_original_attempt_facts(tmp_path,execution_registry):
    root,job,config=scene(tmp_path);run(root,config,'P')
    record,payload=output(root,None)
    worker_delivery.capture(root,job,record)
    original=(record/worker_delivery.FACT_NAME).read_bytes()
    run(root,config,'Q')
    result=worker_delivery.assessment(root,job,payload,record)
    assert result['status']=='incomplete' and result['missing']==['h001.m2']
    assert (record/worker_delivery.FACT_NAME).read_bytes()==original


@pytest.mark.parametrize('rows',[None,[],[dict(mechanism='P',basis='observed',finding='   ')],
    [dict(mechanism='P',basis='observed',finding='A limited observation'),dict(mechanism='P',basis='observed',finding='Repeated label')]])
def test_scientific_finding_tables_are_not_required_for_observed_work(tmp_path,execution_registry,rows):
    root,job,config=scene(tmp_path);run(root,config,'P');run(root,config,'Q')
    record,_=output(root,rows)
    c.seal_result(root,2)
    assert c.read(record/'result-identity.json')['control_outcome']['worker_delivery']['status']=='complete'
    assert len(execution.records(root,2))==1


@pytest.mark.parametrize('rows',['Scientific narrative instead of a table',{'finding':'Partial explanation'},
    [dict(mechanism='P',basis='scientific uncertainty',finding='No classified claim')]])
def test_optional_finding_prose_retains_science_without_automatic_screening_or_rework(tmp_path,execution_registry,rows):
    root,job,config=scene(tmp_path);run(root,config,'P');run(root,config,'Q')
    record,payload=output(root,rows);original=(record/'science-output.json').read_bytes()
    c.seal_result(root,2)
    seal=c.read(record/'result-identity.json');receipt=seal['control_outcome']['worker_delivery']
    assert receipt['status']=='complete' and receipt['reason'] is None
    assert (record/'science-output.json').read_bytes()==original
    assert c.validate(c.workspace(root,2))['mechanism_findings']==rows
    route=controller._route(root,2,'W',c.validate(c.workspace(root,2)),job,1,None)
    assert route[0]=='A'
    from scidiscovery.research_loop import search
    assert {row['status'] for row in search.project(root,3)['mechanisms'].values()}=={'pending'}
    assert search._screen_refs(root,job,seal,search.project(root,3))=={'h001.m1','h001.m2'}
    assert len(execution.records(root,2))==1 and len(experiments_calls(config))==2


@pytest.mark.parametrize('intent,rows,reason',[
    ('review',findings('P',blocked=('Q',)),'scientific_blocked'),
    ('decision',findings('P'),'worker_decision'),
])
def test_blocked_or_explicit_scientific_decision_is_partial_and_never_screened(tmp_path,execution_registry,intent,rows,reason):
    root,job,config=scene(tmp_path);run(root,config,'P')
    record,_=output(root,rows,intent=intent)
    c.seal_result(root,2)
    seal=c.read(record/'result-identity.json')
    assert seal['control_outcome']['worker_delivery']['status']=='partial'
    assert seal['control_outcome']['worker_delivery']['reason']==reason
    route=controller._route(root,2,'W',c.validate(c.workspace(root,2)),job,1,None)
    assert route[0]=='A' and reason in route[2] and 'partial' in route[2]
    from scidiscovery.research_loop import search
    assert {row['status'] for row in search.project(root,3)['mechanisms'].values()}=={'pending'}
    before=tree(root);c.validate(c.workspace(root,2));assert tree(root)==before


def test_complete_findings_use_original_failed_observation_and_validate_never_rebuilds(tmp_path,execution_registry):
    root,job,config=scene(tmp_path);run(root,config,'P',failed=True);run(root,config,'Q')
    record,_=output(root,findings('P','Q'));c.seal_result(root,2)
    result=c.read(record/'result-identity.json')['control_outcome']['worker_delivery']
    assert result['status']=='complete' and not result['missing']
    before=tree(root);c.validate(c.workspace(root,2));assert tree(root)==before
    facts=record/worker_delivery.FACT_NAME;facts.unlink()
    before=tree(root)
    with pytest.raises((ValueError,OSError)):c.validate(c.workspace(root,2))
    assert tree(root)==before and not facts.exists()


def test_unknown_native_and_invalid_json_keep_original_unsealed(tmp_path,execution_registry):
    root,job,config=scene(tmp_path);run(root,config,'P')
    record,_=output(root,findings('P',blocked=('Q',)))
    outcome=c.read(record/'native-outcome.json');outcome['usage']['unknown_steps']=[2]
    outcome['usage']['blocking_unknown_steps']=[2]
    c.write(record/'native-outcome.json',outcome)
    with pytest.raises(ValueError):c.seal_result(root,2)
    assert not (record/'result-identity.json').exists() and not (record/worker_delivery.FACT_NAME).exists()
    outcome['usage']['unknown_steps']=[]
    outcome['usage']['blocking_unknown_steps']=[]
    (record/'science-output.json').write_text('not JSON')
    outcome['raw_ref']=c.file_ref(record/'science-output.json');c.write(record/'native-outcome.json',outcome)
    with pytest.raises(OutputRepairRequired) as caught:c.seal_result(root,2)
    assert not isinstance(caught.value,WorkRepairRequired)
    assert not (record/'result-identity.json').exists()


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


def test_direct_seal_never_captures_while_original_writer_is_live(tmp_path,execution_registry,monkeypatch):
    root,job,config=scene(tmp_path);run(root,config,'P');run(root,config,'Q')
    record,_=output(root,findings('P','Q'));stopped_native(root,record)
    monkeypatch.setattr(runtime,'process_matches',lambda *args:True)
    before=tree(root)
    with pytest.raises((ValueError,BlockingIOError),match='Original (native writer|execution bridge) still owns this attempt'):c.seal_result(root,2)
    assert tree(root)==before
    assert not (record/'checkpoint.json').exists() and not (record/worker_delivery.FACT_NAME).exists()


def test_no_resume_or_node_budget_retains_partial_known_original_without_replacement(tmp_path,execution_registry,monkeypatch):
    root,job,config=scene(tmp_path);run(root,config,'P')
    assert c.settings(root).get('resume_policy') is None and budget.node_limits(root,job) is None
    record,_=output(root,findings('P'));stopped_native(root,record)
    monkeypatch.setattr(runtime,'process_matches',lambda *args:False)
    assert 'budget_slice' not in c.read(record/'native-outcome.json')
    # Replacement eligibility still needs its qualified slice; a partial exit
    # accepts no replacement and uses the exact original turn usage instead.
    with pytest.raises(continuation.ResumeUnavailable,match='worker_resume_usage_unknown'):
        continuation._eligible_original(root,2,[record],successful=True)
    continuation.seal_or_repair(root,2)
    receipt=c.read(record/'result-identity.json')['control_outcome']['worker_delivery']
    assert receipt['status']=='partial' and receipt['reason']=='worker_resume_unsupported'
    ending=checkpoint.stage_end(root,job,record)
    assert ending['work_exit']['kind']=='resume_policy'
    assert len(execution.records(root,2))==1 and len(experiments_calls(config))==1
    before=tree(root);c.validate(c.workspace(root,2));assert tree(root)==before


def experiments_calls(config):
    from scidiscovery.research_loop import experiments
    return experiments.Records(config).calls()


@pytest.fixture
def stopped_deliverables(tmp_path,monkeypatch):
    root=tmp_path/'instance';(root/'jobs').mkdir(parents=True)
    c.write(root/'jobs/001.json',dict(role='H'))
    work=root/'work';(work/'artifacts').mkdir(parents=True)
    record=root/'record';record.mkdir()
    policy={}
    monkeypatch.setattr(c,'settings',lambda *args:policy)
    monkeypatch.setattr(c,'workspace',lambda *args:work)
    monkeypatch.setattr(execution,'record',lambda *args:record)
    monkeypatch.setattr(worker_delivery,'writers_stopped',lambda *args:None)
    first=work/'artifacts/a-first.bin';first.write_bytes(b'first');first.chmod(0o640)
    return root,work,first,policy


@pytest.mark.parametrize('fault',['symlink','hardlink','item_budget','total_budget','nested_experiment'])
def test_collect_materials_rejects_later_invalid_inventory_before_chmod(stopped_deliverables,fault):
    root,work,first,policy=stopped_deliverables
    later=work/'artifacts/z-later.bin'
    if fault=='symlink':later.symlink_to(first)
    elif fault=='hardlink':
        later.write_bytes(b'later output');later.chmod(0o640)
        os.link(later,root/'later-alias')
    elif fault=='nested_experiment':(work/'experiments').symlink_to(work/'artifacts',target_is_directory=True)
    else:
        later.write_bytes(b'later output');later.chmod(0o640)
        policy['max_output_item_bytes' if fault=='item_budget' else 'max_output_total_bytes']=len(b'first')
    before=first.stat().st_mode
    with pytest.raises(ValueError):c.collect_materials(root,1)
    assert first.stat().st_mode==before and first.read_bytes()==b'first'
    if fault in {'item_budget','total_budget'}:assert later.stat().st_mode&0o777==0o640


def test_collect_materials_seals_original_paths_after_complete_inventory(stopped_deliverables):
    root,work,first,policy=stopped_deliverables
    conclusion=work/'experiments/one/conclusion.md';conclusion.parent.mkdir(parents=True)
    conclusion.write_bytes(b'conclusion');conclusion.chmod(0o640)
    identity={path:(path.stat().st_dev,path.stat().st_ino) for path in (first,conclusion)}
    policy.update(max_output_item_bytes=10,max_output_total_bytes=15)
    rows=c.collect_materials(root,1)
    assert {row['path'] for row in rows}=={'inputs/parents/001/artifacts/a-first.bin',
        'inputs/parents/001/artifacts/experiments/one/conclusion.md'}
    assert {row['source'] for row in rows}=={str(first),str(conclusion)}
    for path,original in identity.items():
        assert path.stat().st_mode&0o777==0o400
        assert (path.stat().st_dev,path.stat().st_ino)==original


@pytest.mark.parametrize('managed_kind',['frozen_alias','collision','unavailable'])
def test_collect_materials_validates_managed_outputs_before_mutable_seal(stopped_deliverables,monkeypatch,managed_kind):
    from scidiscovery.research_loop import history
    root,work,first,policy=stopped_deliverables
    c.write(root/'jobs/001.json',dict(role='W'))
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
        rows=c.collect_materials(root,1)
        assert first.stat().st_mode&0o777==0o400
        assert any(row['source']==str(managed) for row in rows)
        assert managed.samefile(root/'retained-alias')
    else:
        with pytest.raises(ValueError,match='collide|unavailable'):c.collect_materials(root,1)
        assert first.stat().st_mode==before
        if managed_kind=='collision':assert duplicate.stat().st_mode&0o777==0o640
    assert managed.stat().st_mode&0o777==0o400 and managed.read_bytes()==b'retained'
