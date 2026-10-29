"""A narrow final-response repair cannot start new managed scientific work."""
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import (configuration_updates, contracts as c,
    execution, experiment_client, experiments)
from execution_fixtures import execution_registry
from test_default_cli_output_repair import cli, nomination_diagnostic, prepare_cli


def accepted_attempts(tmp_path, monkeypatch, continuations, *, output_only=True, automatic=None):
    root=tmp_path/'instance';first=root/'native-evidence/001'
    first.mkdir(parents=True);(root/'jobs').mkdir()
    c.write(root/'jobs/001.json',dict(role='W',step=1))
    task=c.record_ref(root/'jobs/001.json')
    original=dict(task_ref=task)
    c.write(first/'native-intent.json',original)
    intents=[(first,original)]
    for number,body in enumerate(continuations,1):
        record=first/'continuations'/f'{number:03d}';record.mkdir(parents=True)
        intent=dict(body,attempt=number,task_ref=task)
        c.write(record/'native-intent.json',intent);intents.append((record,intent))
    policy=dict(output_only=output_only,output_repair=True)
    if automatic is not None:policy['automatic_recovery']=automatic
    monkeypatch.setattr(experiment_client,'root_for_configuration',lambda *_,**__:root)
    monkeypatch.setattr(execution,'accepted_intents',lambda *_:iter(intents))
    monkeypatch.setattr(execution,'records',lambda *_:[record for record,_ in intents])
    monkeypatch.setattr(configuration_updates,'for_job',lambda *_:dict(resume_policy=policy))
    number=len(intents)-1
    config=dict(scope=dict(step=1),native_attempt=dict(number=number,
        execution_key=c.record_ref(root/'jobs/001.json' if number==0 else intents[number][0]/'native-intent.json')))
    return root,config,intents


@pytest.mark.parametrize('output_only',[False,True])
@pytest.mark.parametrize('automatic',[None,dict(enabled=True,max_continues=2,max_retries=3)])
def test_output_repair_never_borrows_independent_work_permission(tmp_path,monkeypatch,output_only,automatic):
    _,config,_=accepted_attempts(tmp_path,monkeypatch,[dict(continuation_kind='repair',
        output_repair=dict(code='candidate_nominations_incomplete'))],output_only=output_only,automatic=automatic)
    with pytest.raises(experiments.UnsupportedExecution,match='Output repair permits original results only'):
        experiments._require_new_execution(config)


@pytest.mark.parametrize('entry',['accept','compile'])
def test_new_accept_and_compile_reject_before_adapter_or_output_effect(tmp_path,monkeypatch,entry):
    _,config,_=accepted_attempts(tmp_path,monkeypatch,[dict(continuation_kind='repair',
        output_repair=dict(code='missing_output'))])
    output=tmp_path/'new-implementation.json'
    with pytest.raises(experiments.UnsupportedExecution,match='Output repair permits original results only'):
        if entry=='accept':
            experiments.Records.accept.__wrapped__(SimpleNamespace(config=config),object(),{}, {})
        else:
            experiments.compile_implementation.__wrapped__(config,object(),tmp_path/'source.py',{},output)
    assert not output.exists()


def test_latest_output_repair_cannot_borrow_original_client(tmp_path,monkeypatch):
    root,config,_=accepted_attempts(tmp_path,monkeypatch,[dict(continuation_kind='repair',
        output_repair=dict(code='missing_output'))])
    config['native_attempt']=dict(number=0,execution_key=c.record_ref(root/'jobs/001.json'))
    with pytest.raises(experiments.UnsupportedExecution,match='Output repair permits original results only'):
        experiments._require_new_execution(config)


@pytest.mark.parametrize('changed',[dict(number=99,execution_key={}),dict(number=True,execution_key={}),
    dict(number=1,execution_key=dict(path='jobs/001.json'))])
def test_new_effect_requires_exact_accepted_attempt(tmp_path,monkeypatch,changed):
    _,config,_=accepted_attempts(tmp_path,monkeypatch,[dict(continuation_kind='automatic_recovery')])
    config['native_attempt']=changed
    with pytest.raises(experiments.IntegrityError,match='exact accepted native'):
        experiments._require_new_execution(config)


def test_current_work_continue_keeps_new_work_after_historical_output_repair(tmp_path,monkeypatch):
    _,config,_=accepted_attempts(tmp_path,monkeypatch,[dict(continuation_kind='repair',
        output_repair=dict(code='missing_output')),dict(continuation_kind='automatic_recovery',
        automatic_recovery=dict(action='continue'),work_repair=dict(code='worker_delivery_incomplete'))],
        automatic=dict(enabled=True,max_continues=2,max_retries=3))
    experiments._require_new_execution(config)


@pytest.mark.parametrize('output_only',[False,True])
def test_existing_execution_wake_policy_still_bounds_new_work(tmp_path,monkeypatch,output_only):
    _,config,_=accepted_attempts(tmp_path,monkeypatch,[dict(continuation_kind='execution_wake')],output_only=output_only)
    if output_only:
        with pytest.raises(experiments.UnsupportedExecution,match='Output-only execution wake'):
            experiments._require_new_execution(config)
    else:experiments._require_new_execution(config)


@pytest.mark.process_e2e
@pytest.mark.parametrize('default_policy',[False,True],ids=['legacy-output-only','default-independent-auto'])
def test_output_repair_turn_rejects_new_bridge_run_but_keeps_original_reads(tmp_path,execution_registry,default_policy):
    root,_,env=prepare_cli(tmp_path,fault='nomination-layout',fragment=not default_policy,output_repair_probe=True,layout_input_tokens=10)
    policy=c.settings(root)['resume_policy']
    assert policy['output_only'] is True and policy['output_repair'] is True
    if default_policy:assert policy['automatic_recovery']['enabled'] is True
    else:assert policy.get('automatic_recovery') is None
    result=cli(root,'run',env=env)
    assert result['status']=='finish',nomination_diagnostic(root,result,fault='output-repair-new-effect')
    origin,current=execution.records(root,2)
    intent=c.read(current/'native-intent.json')
    assert intent['continuation_kind']=='repair' and intent['output_repair']['code']=='deliverable_path_collision'
    work=c.workspace(root,2);locator=c.read(work/'work/retained-candidate-locator.json')
    query=c.read(work/'work/output-repair-query.json')
    collected=c.read(work/'work/output-repair-collect.json')
    rejected=c.read(work/'work/output-repair-run.json')
    assert query['code']==0 and query['result']['state']=='succeeded'
    assert query['result']['call_id']==locator['call_id']
    assert collected['code']==0 and collected['result']['status']=='collected'
    assert collected['result']['call_id']==locator['call_id']
    assert rejected['code']==2 and rejected['result']['status']=='unavailable'
    assert 'call_id' not in rejected['result']
    calls=experiment_client.record_directory(root,2)/'calls'
    assert [path.name for path in calls.iterdir()]==[locator['call_id']]
    assert c.read(calls/locator['call_id']/'accepted.json')['native_attempt']['number']==0
    assert not (work/'artifacts/output-repair-new-effect.txt').exists()
    assert not (work/'experiments/repair-probe/artifacts/new-managed-effect.txt').exists()
    assert (work/'work/launches').read_text()=='exec\nresume\n'
    retained={path:path.read_bytes() for path in (origin/'science-output.json',
        calls/locator['call_id']/'accepted.json',calls/locator['call_id']/'completed.json')}
    assert cli(root,'run',env=env)['status']=='finish'
    assert all(path.read_bytes()==data for path,data in retained.items())
    assert [path.name for path in calls.iterdir()]==[locator['call_id']]
