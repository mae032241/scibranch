"""Author layout mistakes reopen only the exact completed output contribution."""
import pytest

from scidiscovery.research_loop import (contracts as c, continuation, execution,
    history, worker_delivery, native, candidate_refs, candidate_nominations, formal_review)
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from execution_fixtures import execution_registry
from test_default_cli_output_repair import cli, nomination_diagnostic, prepare_cli


def inventory_scene(tmp_path, monkeypatch):
    root=tmp_path/'instance';work=root/'node';record=root/'record'
    work.mkdir(parents=True);record.mkdir()
    job=dict(role='W',step=1,handoff_protocol=c.HANDOFF_PROTOCOL,direct_materials=[])
    c.write(root/'jobs/001.json',job)
    managed=root/'managed/model.bin';managed.parent.mkdir();managed.write_bytes(b'original');managed.chmod(0o400)
    copied=work/'artifacts/experiments/call-1/artifacts/model.bin'
    copied.parent.mkdir(parents=True);copied.write_bytes(managed.read_bytes())
    outputs=[dict(name='model.bin',path='managed/model.bin',retention='archived')]
    calls=[dict(call_id='call-1',status='known_success')]
    monkeypatch.setattr(c,'settings',lambda *args:{})
    monkeypatch.setattr(c,'workspace',lambda *args:work)
    monkeypatch.setattr(execution,'record',lambda *args:record)
    monkeypatch.setattr(worker_delivery,'writers_stopped',lambda *args:None)
    monkeypatch.setattr(history,'managed_calls',lambda *args:calls)
    monkeypatch.setattr(history,'scientific_outputs',lambda *args:outputs)
    return root,work,record,job,managed,copied,outputs,calls


def test_distinct_sources_with_equal_bytes_require_author_layout_repair(tmp_path,monkeypatch):
    root,work,record,job,managed,copied,outputs,calls=inventory_scene(tmp_path,monkeypatch)
    before={path:(path.stat().st_mode,path.read_bytes()) for path in (managed,copied)}
    with pytest.raises(OutputRepairRequired) as caught:c.collect_materials(root,1)
    assert caught.value.code=='deliverable_path_collision'
    assert 'artifacts/experiments/call-1/artifacts/model.bin' in caught.value.diagnostic
    assert 'Remove redundant copies' in caught.value.diagnostic and str(root) not in caught.value.diagnostic
    assert {path:(path.stat().st_mode,path.read_bytes()) for path in before}==before
    assert not managed.samefile(copied)
    copied.unlink()  # The author decides this copy is redundant.
    rows=c.collect_materials(root,1)
    assert len(rows)==1 and rows[0]['source']==str(managed)


def test_same_source_and_publication_path_are_counted_once(tmp_path,monkeypatch):
    root,work,record,job,managed,copied,outputs,calls=inventory_scene(tmp_path,monkeypatch)
    copied.unlink();outputs.append(dict(outputs[0]))
    monkeypatch.setattr(c,'settings',lambda *args:dict(max_output_total_bytes=managed.stat().st_size))
    rows,mutable=c.material_inventory(root,1)
    assert len(rows)==1 and mutable==[]


@pytest.mark.parametrize('failure',['unsealed_original','original_link','integrity','io'])
def test_original_integrity_and_io_failures_never_become_author_repair(tmp_path,monkeypatch,failure):
    root,work,record,job,managed,copied,outputs,calls=inventory_scene(tmp_path,monkeypatch)
    if failure=='unsealed_original':managed.chmod(0o600)
    elif failure=='original_link':
        link=root/'managed/alias';link.symlink_to(managed);outputs[0]['path']='managed/alias'
    else:
        def reject(*args):
            if failure=='io':raise OSError('unavailable original')
            raise ValueError('original identity changed')
        monkeypatch.setattr(history,'scientific_outputs',reject)
    with pytest.raises((ValueError,OSError)) as caught:c.material_inventory(root,1)
    assert not isinstance(caught.value,OutputRepairRequired)
    assert copied.stat().st_mode & 0o777==0o644


def test_two_owned_files_require_layout_repair_without_merging_content(tmp_path,monkeypatch):
    root,work,record,job,managed,copied,outputs,calls=inventory_scene(tmp_path,monkeypatch)
    calls.clear()
    authored=work/'experiments/call-1/artifacts/model.bin'
    authored.parent.mkdir(parents=True);authored.write_bytes(b'Distinct authored content')
    before={path:path.read_bytes() for path in (authored,copied)}
    with pytest.raises(OutputRepairRequired) as caught:c.material_inventory(root,1)
    assert caught.value.code=='deliverable_path_collision'
    assert 'Separate owned deliverables' in caught.value.diagnostic
    assert all(path.relative_to(work).as_posix() in caught.value.diagnostic for path in before)
    assert {path:path.read_bytes() for path in before}==before


@pytest.mark.parametrize('with_mutable',[False,True])
def test_different_managed_originals_cannot_delegate_collision_to_author(tmp_path,monkeypatch,with_mutable):
    root,work,record,job,managed,copied,outputs,calls=inventory_scene(tmp_path,monkeypatch)
    other=managed.with_name('different-original.bin');other.write_bytes(managed.read_bytes());other.chmod(0o400)
    outputs.append(dict(outputs[0],path=other.relative_to(root).as_posix()))
    if not with_mutable:copied.unlink()
    before={path:path.read_bytes() for path in (managed,other)}
    with pytest.raises(ValueError,match='Different managed originals') as caught:c.material_inventory(root,1)
    assert not isinstance(caught.value,OutputRepairRequired)
    assert {path:path.read_bytes() for path in before}==before


def test_same_source_first_seen_owned_retains_managed_category_for_other_collisions(tmp_path,monkeypatch):
    root,work,record,job,managed,copied,outputs,calls=inventory_scene(tmp_path,monkeypatch)
    copied.chmod(0o400)
    outputs[0]['path']=copied.relative_to(root).as_posix()
    outputs.append(dict(outputs[0],path=managed.relative_to(root).as_posix()))
    before={path:(path.stat().st_mode,path.read_bytes()) for path in (managed,copied)}
    with pytest.raises(ValueError,match='Different managed originals') as caught:c.material_inventory(root,1)
    assert not isinstance(caught.value,OutputRepairRequired)
    assert {path:(path.stat().st_mode,path.read_bytes()) for path in before}==before


def test_output_request_uses_pure_layout_diagnosis_and_rejects_valid_or_unknown_output(tmp_path,monkeypatch):
    root,work,record,job,managed,copied,outputs,calls=inventory_scene(tmp_path,monkeypatch)
    raw=record/'science-output.json'
    c.write(raw,dict(summary='Retained',scientific_result='One observation',limitations=[],stage_intent='review'))
    outcome=dict(returncode=0,stop_reason=None,usage=dict(unknown_steps=[]),raw_ref=c.file_ref(raw))
    c.write(record/'native-outcome.json',outcome)
    monkeypatch.setattr(continuation,'validate_task',lambda *args:job)
    monkeypatch.setattr(candidate_refs,'control_payload',lambda job,science,**kwargs:science)
    monkeypatch.setattr(formal_review,'validate_choice',lambda *args:None)
    monkeypatch.setattr(worker_delivery,'require_delivery',lambda *args,**kwargs:None)
    monkeypatch.setattr(candidate_nominations,'require',lambda *args:None)
    request=continuation.output_request(root,1,record,source_attempt=0)
    assert request['code']=='deliverable_path_collision' and request['raw_ref']==outcome['raw_ref']
    assert copied.stat().st_mode & 0o777==0o644
    copied.unlink()
    with pytest.raises(ValueError,match='valid final response'):
        continuation.output_request(root,1,record,source_attempt=0)
    outcome['returncode']=None;c.write(record/'native-outcome.json',outcome)
    with pytest.raises(ValueError,match='known completed original turn'):
        continuation.output_request(root,1,record,source_attempt=0)


def test_repair_diagnostic_is_bounded_in_utf8_bytes():
    diagnosis=OutputRepairRequired('layout','界'*512).diagnostic
    assert diagnosis and len(diagnosis.encode('utf-8'))<=512


@pytest.mark.parametrize('failure',['raw_identity','invalid_fields','io'])
def test_layout_diagnosis_does_not_mask_original_response_or_io_failure(tmp_path,monkeypatch,failure):
    root,work,record,job,managed,copied,outputs,calls=inventory_scene(tmp_path,monkeypatch)
    raw=record/'science-output.json'
    science=dict(summary='Retained',scientific_result='One observation',limitations=[],stage_intent='review')
    if failure=='invalid_fields':science.pop('summary')
    c.write(raw,science)
    outcome=dict(returncode=0,stop_reason=None,usage=dict(unknown_steps=[]),raw_ref=c.file_ref(raw))
    c.write(record/'native-outcome.json',outcome)
    if failure=='raw_identity':raw.write_text('Replaced raw original')
    monkeypatch.setattr(continuation,'validate_task',lambda *args:job)
    monkeypatch.setattr(candidate_refs,'control_payload',lambda job,science,**kwargs:science)
    monkeypatch.setattr(formal_review,'validate_choice',lambda *args:None)
    monkeypatch.setattr(worker_delivery,'require_delivery',lambda *args,**kwargs:None)
    monkeypatch.setattr(candidate_nominations,'require',lambda *args:None)
    if failure=='invalid_fields':
        request=continuation.output_request(root,1,record,source_attempt=0)
        assert request['code']=='invalid_fields'
    else:
        if failure=='io':
            def unavailable(*args):raise OSError('unavailable original')
            monkeypatch.setattr(history,'scientific_outputs',unavailable)
        with pytest.raises((ValueError,OSError)) as caught:
            continuation.output_request(root,1,record,source_attempt=0)
        assert not isinstance(caught.value,OutputRepairRequired)
    assert copied.stat().st_mode & 0o777==0o644


@pytest.mark.process_e2e
@pytest.mark.parametrize('fragment',[False,True])
def test_completed_valid_w_removes_own_copy_and_seals_original_without_replay(tmp_path,execution_registry,fragment):
    root,home,env=prepare_cli(tmp_path,fault='nomination-layout',fragment=fragment,max_tokens=4000000,output_repair_probe=True)
    settings=(root/'settings.json').read_bytes()
    result=cli(root,'run',env=env)
    assert result['status']=='finish',nomination_diagnostic(root,result,fault='nomination-layout',fragment=fragment)
    initial,repaired=execution.records(root,2)
    intent=c.read(repaired/'native-intent.json');old=c.read(initial/'native-intent.json')
    assert intent['continuation_kind']=='repair' and intent['output_repair']['code']=='deliverable_path_collision'
    assert intent.get('work_repair') is None and intent.get('automatic_recovery') is None
    assert intent['task_ref']==old['task_ref'] and intent.get('native_profile')==old.get('native_profile')
    assert intent['session']['home']==str(home)
    assert intent['session']['thread_id']==c.read(initial/'native-outcome.json')['thread_id']
    assert intent['budget_slice']['previous_tokens']==2040305
    if fragment:
        assert (initial/'stage-end.json').is_file()
        assert intent['budget_slice']['previous_tokens']>intent['budget_slice']['limits']['max_observed_tokens']
    assert c.read(initial/'science-output.json')==c.read(repaired/'science-output.json')
    assert c.read(initial/'native-outcome.json')['raw_ref']==c.file_ref(initial/'science-output.json')
    work=c.workspace(root,2);locator=c.read(work/'work/retained-candidate-locator.json')
    assert (work/'work/launches').read_text()=='exec\nresume\n'
    output_name='model-0.bin'
    assert not (work/'artifacts/experiments'/locator['call_id']/'artifacts'/output_name).exists()
    from scidiscovery.research_loop import experiment_client
    calls=experiment_client.record_directory(root,2)/'calls'
    assert [path.name for path in calls.iterdir()]==[locator['call_id']]
    owner=calls/locator['call_id']
    assert c.read(owner/'accepted.json')['native_attempt']['number']==0
    managed=work/'work/retained-experiments'/locator['call_id']/'outputs'/output_name
    assert managed.read_text()=='original model 0'
    seal=c.read(initial/'result-identity.json')
    assert c.read(root/'jobs/003.json')['role']=='A'
    assert c.validate(work)['stage_intent']=='review'
    assert len([row for row in seal['materials'] if row['path']==f'inputs/parents/002/artifacts/experiments/{locator["call_id"]}/artifacts/{output_name}'])==1
    query=c.read(work/'work/output-repair-query.json');collect=c.read(work/'work/output-repair-collect.json')
    assert query['code']==collect['code']==0
    rejected=c.read(work/'work/output-repair-run.json')
    assert rejected['code']!=0 and not (work/'artifacts/output-repair-new-effect.txt').exists()
    retained={path:path.read_bytes() for path in (initial/'science-output.json',initial/'native-intent.json',
        initial/'native-outcome.json',initial/'usage.json',owner/'accepted.json',owner/'completed.json',owner/'execution-observation.json',managed)}
    usage=native.usage(root)
    assert cli(root,'run',env=env)['status']=='finish'
    assert execution.records(root,2)==[initial,repaired] and native.usage(root)==usage
    assert (root/'settings.json').read_bytes()==settings
    assert all(path.read_bytes()==data for path,data in retained.items())


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault,disabled,attempts',[
    ('nomination-layout',True,1),('nomination-layout-repeat',False,3)])
def test_layout_repair_respects_frozen_disable_and_attempt_cap(tmp_path,execution_registry,fault,disabled,attempts):
    root,_,env=prepare_cli(tmp_path,fault=fault,explicit_false=disabled,max_tokens=4000000)
    result=cli(root,'run',env=env)
    assert result['status']!='finish',nomination_diagnostic(root,result,fault=fault)
    assert len(execution.records(root,2))==attempts
    work=c.workspace(root,2);locator=c.read(work/'work/retained-candidate-locator.json')
    assert (work/'work/launches').read_text()=='exec\n'+'resume\n'*(attempts-1)
    assert not (root/'jobs/003.json').exists()
    assert (work/'work/retained-experiments'/locator['call_id']/'outputs/model-0.bin').read_text()=='original model 0'
