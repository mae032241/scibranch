"""Author layout mistakes reopen only the exact completed output contribution."""
import pytest

from scidiscovery.research_loop import contracts as c, execution, native
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from execution_fixtures import execution_registry
from test_default_cli_output_repair import cli, nomination_diagnostic, prepare_cli


def test_repair_diagnostic_is_bounded_in_utf8_bytes():
    diagnosis=OutputRepairRequired('layout','界'*512).diagnostic
    assert diagnosis and len(diagnosis.encode('utf-8'))<=512


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
