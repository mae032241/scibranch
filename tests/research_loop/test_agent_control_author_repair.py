"""Consumed author errors return to the original finite native session."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c, continuation, execution, experiment_client, native
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from execution_fixtures import execution_registry
from test_default_cli_output_repair import cli, prepare_cli, nomination_diagnostic


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault,code',[('author-focus','science_focus_unauthorized'),
    ('author-selection','implementation_unresolved')])
def test_consumed_author_choice_resumes_same_task_and_session_before_worker(tmp_path,execution_registry,fault,code):
    root,home,env=prepare_cli(tmp_path,fault=fault)
    frozen=(root/'settings.json').read_bytes()
    result=cli(root,'run',env=env)
    assert result['status']=='finish',nomination_diagnostic(root,result,fault=fault)
    first,repaired=execution.records(root,1)
    old=c.read(first/'native-intent.json');current=c.read(repaired/'native-intent.json')
    request=continuation.output_request(root,1,first,source_attempt=0)
    assert request['code']==code and current['output_repair']==request
    assert current['task_ref']==old['task_ref']==c.record_ref(root/'jobs/001.json')
    assert current.get('native_profile')==old.get('native_profile')
    assert current['session']['home']==str(home)
    assert current['session']['thread_id']==c.read(first/'native-outcome.json')['thread_id']
    assert current['budget_slice']['previous_tokens']==14
    assert code in (c.workspace(root,1)/'work/author-repair-prompt.txt').read_text()
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\nresume\n'
    assert [c.read(root/'jobs'/f'{step:03d}.json')['role'] for step in (1,2,3)]==['H','W','A']
    assert all(not c.read(root/'jobs'/f'{step:03d}.json').get('author_correction') for step in (1,2,3))
    assert not (root/'jobs/004.json').exists()
    calls=experiment_client.record_directory(root,2)/'calls'
    locator=c.read(c.workspace(root,2)/'work/retained-candidate-locator.json')
    assert [path.name for path in calls.iterdir()]==[locator['call_id']]
    retained={path:path.read_bytes() for path in (first/'science-output.json',first/'native-intent.json',
        first/'native-outcome.json',calls/locator['call_id']/'accepted.json',calls/locator['call_id']/'completed.json')}
    usage=native.usage(root)
    assert cli(root,'run',env=env)['status']=='finish'
    assert execution.records(root,1)==[first,repaired] and native.usage(root)==usage
    assert (root/'settings.json').read_bytes()==frozen
    assert all(path.read_bytes()==raw for path,raw in retained.items())


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault,disabled,attempts',[('author-focus',True,1),('author-focus-repeat',False,3)])
def test_consumed_choice_repair_preserves_disabled_policy_and_finite_attempts(tmp_path,execution_registry,fault,disabled,attempts):
    root,_,env=prepare_cli(tmp_path,fault=fault,explicit_false=disabled)
    result=cli(root,'run',env=env)
    assert result['status']!='finish'
    assert len(execution.records(root,1))==attempts
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'+'resume\n'*(attempts-1)
    assert not (root/'jobs/002.json').exists()


@pytest.mark.parametrize('damage,field,expectation',[
    ('missing','summary','missing required field summary'),
    ('type','limitations','expected array'),
    ('enum','next_action','expected one of'),
])
def test_required_field_diagnosis_identifies_short_repair_without_rewriting_bytes(tmp_path,damage,field,expectation):
    value=dict(summary='Retained science',scientific_result='Original evidence',limitations=[],
        next_action='analyze',route_reason='Read the retained comparison.')
    if damage=='missing':value.pop(field)
    elif damage=='type':value[field]='wrong type'
    else:value[field]='not-an-action'
    path=tmp_path/'original.json';c.write(path,value);original=path.read_bytes()
    with pytest.raises(OutputRepairRequired) as caught:c.science_payload(path,'H')
    assert expectation in caught.value.diagnostic
    assert field in caught.value.diagnostic and len(caught.value.diagnostic.encode('utf-8'))<=512
    assert path.read_bytes()==original


def test_diagnostic_display_replaces_invalid_unicode_without_creating_a_science_gate():
    error=OutputRepairRequired('choice','Correct focus \ud800'+'界'*512)
    assert len(error.diagnostic.encode('utf-8'))<=512 and error.code=='choice'


@pytest.mark.process_e2e
@pytest.mark.parametrize('ending',['decision','blocked'])
def test_terminal_typed_caller_refusal_resumes_original_worker_and_executes_missing_work_once(tmp_path,execution_registry,ending):
    fault='completed-caller-'+ending
    root,home,env=prepare_cli(tmp_path,fault=fault)
    settings=(root/'settings.json').read_bytes()
    result=cli(root,'run',env=env)
    assert result['status']=='finish',nomination_diagnostic(root,result,fault=fault)
    first,repaired=execution.records(root,2)
    old=c.read(first/'native-intent.json');intent=c.read(repaired/'native-intent.json')
    assert intent['continuation_kind']=='automatic_recovery'
    assert intent['work_repair']==continuation.output_request(root,2,first,source_attempt=0)
    assert intent['work_repair']['code']=='worker_delivery_incomplete'
    assert 'h001.m2' in intent['work_repair']['diagnostic'] and 'options' in intent['work_repair']['diagnostic']
    assert intent['task_ref']==old['task_ref'] and intent.get('native_profile')==old.get('native_profile')
    assert intent['session']['thread_id']==c.read(first/'native-outcome.json')['thread_id']
    assert intent['session']['home']==str(home) and intent['budget_slice']['previous_tokens']==14
    work=c.workspace(root,2)
    assert (work/'work/launches').read_text()=='exec\nresume\n'
    refused=c.read(work/'work/caller-refusal.json')
    assert refused['status']=='caller_error' and refused['accepted'] is False and 'call_id' not in refused
    calls=experiment_client.record_directory(root,2)/'calls'
    first_id=c.read(work/'work/retained-candidate-locator.json')['call_id']
    second_id=c.read(work/'work/second-original-call.json')['call_id']
    assert {path.name for path in calls.iterdir()}=={first_id,second_id}
    assert c.read(calls/first_id/'accepted.json')['native_attempt']['number']==0
    assert c.read(calls/second_id/'accepted.json')['native_attempt']['number']==1
    assert len(c.read(first/'work-evidence.json')['evidence'])==1
    assert len(c.read(repaired/'work-evidence.json')['evidence'])==2
    assert c.read(first/'result-identity.json')['control_outcome']['worker_delivery']['status']=='complete'
    frozen={path:path.read_bytes() for path in (first/'science-output.json',first/'native-outcome.json',
        calls/first_id/'accepted.json',calls/first_id/'completed.json',calls/second_id/'accepted.json',calls/second_id/'completed.json')}
    usage=native.usage(root)
    assert cli(root,'run',env=env)['status']=='finish'
    assert execution.records(root,2)==[first,repaired] and native.usage(root)==usage
    assert (root/'settings.json').read_bytes()==settings
    assert all(path.read_bytes()==raw for path,raw in frozen.items())


@pytest.mark.process_e2e
def test_same_turn_caller_correction_with_accepted_result_does_not_resume_worker(tmp_path,execution_registry):
    root,_,env=prepare_cli(tmp_path,fault='completed-caller-corrected')
    result=cli(root,'run',env=env)
    assert result['status']=='finish',nomination_diagnostic(root,result,fault='completed-caller-corrected')
    first,=execution.records(root,2)
    assert len(c.read(first/'work-evidence.json')['evidence'])==2
    assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'
    assert len(list((experiment_client.record_directory(root,2)/'calls').iterdir()))==2


@pytest.mark.process_e2e
def test_improve_stage_rejected_followup_is_repaired_despite_retained_observation(tmp_path,execution_registry):
    fault='completed-caller-improve-decision'
    root,_,env=prepare_cli(tmp_path,fault=fault)
    result=cli(root,'run',env=env)
    assert result['status']=='finish',nomination_diagnostic(root,result,fault=fault)
    first,repaired=execution.records(root,2)
    job=c.read(root/'jobs/002.json')
    assert job['search']['focus']['action']=='improve' and job['search']['coverage_obligation']==[]
    assert len(c.read(first/'work-evidence.json')['evidence'])==1
    intent=c.read(repaired/'native-intent.json')
    assert intent['work_repair']['code']=='worker_delivery_incomplete'
    assert 'h001.m1' in intent['work_repair']['diagnostic']
    calls=experiment_client.record_directory(root,2)/'calls'
    work=c.workspace(root,2)
    first_id=c.read(work/'work/retained-candidate-locator.json')['call_id']
    second_id=c.read(work/'work/second-original-call.json')['call_id']
    assert first_id!=second_id and {path.name for path in calls.iterdir()}=={first_id,second_id}
    assert c.read(calls/first_id/'accepted.json')['native_attempt']['number']==0
    assert c.read(calls/second_id/'accepted.json')['native_attempt']['number']==1
    assert c.read(first/'result-identity.json')['control_outcome']['worker_delivery']['status']=='not_applicable'
    assert (work/'work/launches').read_text()=='exec\nresume\n'


@pytest.mark.process_e2e
def test_no_search_worker_repairs_typed_unaccepted_request_without_invented_mechanism(tmp_path,execution_registry):
    fault='caller-no-search'
    root,_,env=prepare_cli(tmp_path,fault=fault)
    result=cli(root,'run',env=env)
    assert result['status']=='finish',nomination_diagnostic(root,result,fault=fault)
    first,repaired=execution.records(root,2)
    job=c.read(root/'jobs/002.json')
    assert not job.get('search')
    intent=c.read(repaired/'native-intent.json')
    assert intent['work_repair']['code']=='worker_delivery_incomplete'
    assert 'unaccepted run request' in intent['work_repair']['diagnostic']
    assert c.read(first/'work-evidence.json')['evidence']==[]
    work=c.workspace(root,2);call=c.read(work/'work/only-accepted-call.json')['call_id']
    calls=experiment_client.record_directory(root,2)/'calls'
    assert [path.name for path in calls.iterdir()]==[call]
    assert c.read(calls/call/'accepted.json')['native_attempt']['number']==1
    assert c.read(first/'result-identity.json')['control_outcome']['worker_delivery']['status']=='not_applicable'
    assert (work/'work/launches').read_text()=='exec\nresume\n'


@pytest.mark.process_e2e
def test_typed_compile_json_refusal_resumes_original_worker_with_local_execution(tmp_path,execution_registry):
    fault='caller-compile-no-search'
    root,home,env=prepare_cli(tmp_path,fault=fault)
    result=cli(root,'run',env=env)
    assert result['status']=='finish',nomination_diagnostic(root,result,fault=fault)
    first,repaired=execution.records(root,2)
    intent=c.read(repaired/'native-intent.json');old=c.read(first/'native-intent.json')
    assert intent['continuation_kind']=='automatic_recovery'
    assert intent['work_repair']==continuation.output_request(root,2,first,source_attempt=0)
    assert intent['work_repair']['code']=='worker_delivery_incomplete'
    assert 'unaccepted compile request' in intent['work_repair']['diagnostic']
    assert 'JSON' in intent['work_repair']['diagnostic']
    assert intent['task_ref']==old['task_ref'] and intent.get('native_profile')==old.get('native_profile')
    assert intent['session']['home']==str(home)
    assert intent['session']['thread_id']==c.read(first/'native-outcome.json')['thread_id']
    assert intent['budget_slice']['previous_tokens']==14
    work=c.workspace(root,2);refused=c.read(work/'work/caller-refusal.json')
    assert refused['status']=='caller_error' and refused['accepted'] is False and 'call_id' not in refused
    assert (work/'code/scientific-files.json').read_text()=='{"broken":'
    assert not (work/'code/compiled.json').exists()
    call=c.read(work/'work/only-accepted-call.json')['call_id']
    calls=experiment_client.record_directory(root,2)/'calls'
    assert [path.name for path in calls.iterdir()]==[call]
    assert c.read(calls/call/'accepted.json')['native_attempt']['number']==1
    assert (work/'work/launches').read_text()=='exec\nresume\n'
    retained={path:path.read_bytes() for path in (first/'science-output.json',first/'native-outcome.json',
        calls/call/'accepted.json',calls/call/'completed.json')}
    usage=native.usage(root)
    assert cli(root,'run',env=env)['status']=='finish'
    assert execution.records(root,2)==[first,repaired] and native.usage(root)==usage
    assert all(path.read_bytes()==data for path,data in retained.items())


@pytest.mark.process_e2e
def test_typed_unknown_command_resumes_original_worker_to_choose_valid_local_command(tmp_path,execution_registry):
    fault='caller-command-no-search'
    root,home,env=prepare_cli(tmp_path,fault=fault)
    result=cli(root,'run',env=env)
    assert result['status']=='finish',nomination_diagnostic(root,result,fault=fault)
    first,repaired=execution.records(root,2)
    old=c.read(first/'native-intent.json');intent=c.read(repaired/'native-intent.json')
    assert intent['continuation_kind']=='automatic_recovery'
    assert intent['work_repair']==continuation.output_request(root,2,first,source_attempt=0)
    assert 'unaccepted rnu request' in intent['work_repair']['diagnostic']
    assert intent['task_ref']==old['task_ref'] and intent.get('native_profile')==old.get('native_profile')
    assert intent['session']['home']==str(home)
    assert intent['session']['thread_id']==c.read(first/'native-outcome.json')['thread_id']
    assert intent['budget_slice']['previous_tokens']==14
    work=c.workspace(root,2);refused=c.read(work/'work/caller-refusal.json')
    assert refused['status']=='caller_error' and refused['accepted'] is False and 'call_id' not in refused
    assert c.read(first/'science-output.json')['stage_intent']=='decision'
    call=c.read(work/'work/only-accepted-call.json')['call_id']
    calls=experiment_client.record_directory(root,2)/'calls'
    assert [path.name for path in calls.iterdir()]==[call]
    assert c.read(calls/call/'accepted.json')['native_attempt']['number']==1
    assert (work/'work/launches').read_text()=='exec\nresume\n'
    frozen={path:path.read_bytes() for path in (first/'science-output.json',first/'native-outcome.json',
        calls/call/'accepted.json',calls/call/'completed.json')}
    usage=native.usage(root)
    assert cli(root,'run',env=env)['status']=='finish'
    assert execution.records(root,2)==[first,repaired] and native.usage(root)==usage
    assert all(path.read_bytes()==data for path,data in frozen.items())


@pytest.mark.process_e2e
def test_oversized_unknown_command_token_repairs_from_original_empty_argv_host_claim(tmp_path,execution_registry,monkeypatch):
    import test_default_cli_output_repair as fixture
    # Construct the large token inside native Python, avoiding the OS argv
    # limit. The real bridge client publishes its bounded host notification.
    original="  outcome=subprocess.run(arguments,capture_output=True,text=True)"
    replacement="""  if config['fault']=='caller-command-no-search' and not resume:
   from types import SimpleNamespace
   from scidiscovery.research_loop.worker_execution_bridge import client
   reply=client(work/SUBMISSION_DIRECTORY,['rnu'+'x'*300000])
   outcome=SimpleNamespace(returncode=reply['code'],stdout=json.dumps(reply['result']),stderr='')
  else:outcome=subprocess.run(arguments,capture_output=True,text=True)"""
    assert fixture.NATIVE.count(original)==1
    source=fixture.NATIVE.replace(original,replacement).replace(
        "assert 'unaccepted rnu request' in prompt","assert 'unaccepted command request' in prompt")
    monkeypatch.setattr(fixture,'NATIVE',source)
    fault='caller-command-no-search'
    root,home,env=prepare_cli(tmp_path,fault=fault)
    result=cli(root,'run',env=env)
    assert result['status']=='finish',nomination_diagnostic(root,result,fault='oversized-unknown-command')
    first,repaired=execution.records(root,2)
    intent=c.read(repaired/'native-intent.json');old=c.read(first/'native-intent.json')
    request=continuation.output_request(root,2,first,source_attempt=0)
    assert intent['work_repair']==request and request['code']=='worker_delivery_incomplete'
    assert 'unaccepted command request' in request['diagnostic']
    config=c.read(first/'experiment-config.json')
    claim=Path(config['record_dir']).parent/'worker-submissions/S001'
    submission=c.read(claim/'submit.json');response=c.read(claim/'response.json')
    assert submission['arguments']==[] and submission['client_error']=='submission_frame_too_large'
    assert response['request']==submission and response['result']['status']=='caller_error'
    assert response['result']['accepted'] is False and c.read(claim/'outcome.json')['calls']==[]
    assert intent['task_ref']==old['task_ref'] and intent.get('native_profile')==old.get('native_profile')
    assert intent['session']['home']==str(home)
    assert intent['session']['thread_id']==c.read(first/'native-outcome.json')['thread_id']
    assert intent['budget_slice']['previous_tokens']==14
    work=c.workspace(root,2);call=c.read(work/'work/only-accepted-call.json')['call_id']
    calls=experiment_client.record_directory(root,2)/'calls'
    assert [path.name for path in calls.iterdir()]==[call]
    assert c.read(calls/call/'accepted.json')['native_attempt']['number']==1
    assert (work/'work/launches').read_text()=='exec\nresume\n'
    frozen={path:path.read_bytes() for path in (claim/'submit.json',claim/'binding.json',claim/'response.json',
        claim/'outcome.json',first/'science-output.json',first/'native-outcome.json',calls/call/'accepted.json',calls/call/'completed.json')}
    usage=native.usage(root)
    assert cli(root,'run',env=env)['status']=='finish'
    assert execution.records(root,2)==[first,repaired] and native.usage(root)==usage
    assert all(path.read_bytes()==data for path,data in frozen.items())
