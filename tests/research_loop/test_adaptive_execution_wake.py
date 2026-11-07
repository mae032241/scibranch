"""Ordinary W completion wakes retain one bounded exploration, without models."""
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import (budget, continuation, contracts as c,
    execution, experiments as e, managed_wait, native)
from test_composed_execution_results import composed_registry
from test_managed_execution_wait import run_cli, standard_instance


@pytest.mark.process_e2e
@pytest.mark.parametrize('repeat_output_repair',[False,True],ids=['three-batches','repairs-after-three-wakes'])
def test_three_async_batches_share_one_w_task_and_only_repairs_spend_repair_attempts(
        tmp_path,monkeypatch,composed_registry,repeat_output_repair):
    root,home=standard_instance(tmp_path,monkeypatch,adaptive_batches=3,same_focus=True,
        repeat_output_repair=repeat_output_repair)
    waits=[];originals={};bindings=[];scopes=[];calls=[];snapshots=[]

    def terminal_for_current_batch(record,config,call):
        number=len(waits)
        expected=execution.origin(root,2) if number==0 else execution.origin(root,2)/'continuations'/f'{number:03d}'
        assert record==expected
        assert len(execution.records(root,2))==number+1
        assert not (root/'jobs/003.json').exists()
        job=c.read(root/'jobs/002.json')
        assert job['role']=='W' and job['search']['focus']['action']=='improve'
        work=c.workspace(root,2)
        if not originals:
            paths=(root/'settings.json',root/'settings-identity.json',root/'jobs/002.json',
                work/'science-task.json',execution.origin(root,2)/'science-result.schema.json',home/'config.toml')
            originals.update({path:path.read_bytes() for path in paths})
        assert all(path.read_bytes()==value for path,value in originals.items())
        intent=c.read(record/'native-intent.json')
        if number==0:
            bindings.append({key:value for key,value in intent.items()
                if key not in {'step','task_ref','prepared_at','budget_slice','search_slice'}})
        else:
            assert all(intent.get(key)==value for key,value in bindings[0].items())
            assert intent['continuation_kind']=='execution_wake'
            assert intent['session']['home']==str(home)
            assert intent['session']['thread_id']=='11111111-2222-4333-8444-555555555555'
            assert intent['budget_slice']['previous_tokens']==12*number
            assert intent['budget_slice']['previous_seconds']>=snapshots[-1]['node_seconds']
        assert execution.home(root,2,number)==home
        assert len(e.Records(config).calls())==number+1 and call not in calls
        if scopes:assert config['scope']==scopes[0]
        scopes.append(config['scope'])
        accepted=e.Records(config).accepted(call)
        assert accepted['native_attempt']['number']==number
        assert accepted['mechanism_binding']['mechanism_ref']=='h001.m1'
        directory=e.Records(config).directory(call)
        originals.update({path:path.read_bytes() for path in (directory/'accepted.json',directory/'submission.json')})
        for previous in calls:
            observed=c.read(e.Records(config).directory(previous)/'execution-observation.json')
            original_accepted=c.read(Path(observed['accepted']['path']))
            e.verify_observation(observed,lambda proof:Path(proof['path']),interface=original_accepted['interface'])
            assert observed['status']=='known_success'
        snapshot=budget.snapshot(root)
        node=c.read(record/'native-outcome.json')['budget_slice']
        assert node['observed_tokens']==12*(number+1) and node['dispatches']==number+1
        assert budget.used_steps(root)==number+2
        if snapshots:
            assert snapshot['consumed_seconds']>=snapshots[-1]['consumed_seconds']
            assert snapshot['advance_seconds']>=snapshots[-1]['advance_seconds']
            assert native.usage(root)['observed_tokens']>=snapshots[-1]['observed_tokens']+12
        snapshots.append(dict(snapshot,node_seconds=node['advance_seconds'],observed_tokens=native.usage(root)['observed_tokens']))
        calls.append(call);waits.append(record)

    reply=run_cli(root,on_wait=terminal_for_current_batch)
    result=json.loads(reply.stdout)
    assert len(waits)==len(calls)==3,(reply.stdout,reply.stderr)
    chain=execution.records(root,2)
    assert [c.read(path/'native-intent.json').get('continuation_kind') for path in chain[1:4]]==['execution_wake']*3
    assert all(path.read_bytes()==value for path,value in originals.items())
    assert [item['call_id'] for item in c.read(c.workspace(root,2)/'work/managed-calls.json')]==calls
    assert len({item['payload'] for item in c.read(c.workspace(root,2)/'work/managed-calls.json')})==3
    assert len(e.Records(c.read(chain[0]/'experiment-config.json')).calls())==3
    assert execution.validate_records(root,2)==chain
    if repeat_output_repair:
        assert reply.returncode==2 and result['status']=='output_repair_unavailable',result
        assert result['reason']=='worker_resume_limit'
        assert [c.read(path/'native-intent.json')['continuation_kind'] for path in chain[4:]]==['repair','repair']
        assert [c.read(path/'native-intent.json')['budget_slice']['previous_tokens'] for path in chain[1:]]==[12,24,36,48,60]
        refusals=(c.workspace(root,2)/'work/repair-refusals.jsonl').read_text().splitlines()
        assert len(refusals)==2 and all(json.loads(row)['status']=='unavailable' for row in refusals)
        assert not (root/'jobs/003.json').exists()
        assert budget.used_steps(root)==7
    else:
        assert reply.returncode==0 and result['status']=='wait',(reply.stdout,reply.stderr)
        assert len(chain)==4 and budget.used_steps(root)==6
        assert c.read(root/'jobs/003.json')['role']=='A'
        assert c.read(chain[-1]/'native-outcome.json')['budget_slice']['observed_tokens']==48
        c.validate(c.workspace(root,2))


@pytest.mark.process_e2e
def test_temporary_native_failure_after_full_wake_recovers_same_task_without_replaying_execution(
        tmp_path,monkeypatch,composed_registry):
    root,home=standard_instance(tmp_path,monkeypatch,adaptive_batches=3,same_focus=True,
        temporary_wake_failure=True)
    calls=[];attempts=[];originals={}
    def complete(record,config,call):
        assert not (root/'jobs/003.json').exists()
        assert call not in calls
        intent=c.read(record/'native-intent.json')
        attempts.append(intent.get('attempt',0));calls.append(call)
        if len(calls)>1:
            assert intent['session']['home']==str(home)
            assert intent['budget_slice']['previous_tokens']==12*(len(calls)-1)
        directory=e.Records(config).directory(call)
        originals.update({path:path.read_bytes() for path in (directory/'accepted.json',directory/'submission.json')})
    reply=run_cli(root,on_wait=complete)
    result=json.loads(reply.stdout)
    assert reply.returncode==0 and result['status']=='wait',(reply.stdout,reply.stderr)
    assert attempts==[0,2,3] and len(set(calls))==3
    chain=execution.records(root,2)
    assert [c.read(path/'native-intent.json')['continuation_kind'] for path in chain[1:]]==[
        'execution_wake','automatic_recovery','execution_wake','execution_wake']
    recovered=c.read(chain[2]/'native-intent.json')
    assert recovered['automatic_recovery']['action']=='retry'
    assert recovered['automatic_recovery']['source_attempt']==1
    assert recovered['session']==c.read(chain[1]/'native-intent.json')['session']
    assert not (chain[1]/'usage.json').exists()
    assert c.read(chain[1]/'native-outcome.json')['terminal_observed'] is False
    assert [c.read(path/'native-intent.json')['budget_slice']['previous_tokens'] for path in chain[1:]]==[12,12,24,36]
    assert c.read(chain[-1]/'native-outcome.json')['budget_slice']['observed_tokens']==48
    usage=native.usage(root)
    assert 2 in usage['unknown_steps'] and usage['unknown_step_count']>0
    assert usage['blocking_unknown_count']==0 and usage['accounting_basis']=='observed_lower_bound'
    assert all(path.read_bytes()==value for path,value in originals.items())
    assert len(c.read(c.workspace(root,2)/'work/consumed-output-refs.json'))==3
    assert budget.used_steps(root)==7 and execution.validate_records(root,2)==chain


@pytest.mark.parametrize('changed',[dict(role='A'),dict(formal_review={'requested':True}),
    dict(final_only=True),dict(final_report={'source_cutoff':0}),dict(purpose='closeout')])
def test_completion_wake_cannot_grant_ordinary_work_to_special_task(tmp_path,monkeypatch,changed):
    c.write(tmp_path/'jobs/001.json',dict(dict(step=1,role='W'),**changed))
    monkeypatch.setattr(continuation,'validate_wake_policy',lambda *_:None)
    monkeypatch.setattr(managed_wait,'validate',lambda *_:pytest.fail('Special task cannot acquire a waiting grant'))
    with pytest.raises(ValueError,match='original ordinary W task'):
        continuation.validate_wake(tmp_path,1,{},tmp_path/'prior')


@pytest.mark.parametrize('source',[dict(continuation_kind='repair',output_repair={}),dict(continuation_kind='author_closeout')])
def test_completion_wake_cannot_expand_json_repair_or_author_closeout(tmp_path,monkeypatch,source):
    c.write(tmp_path/'jobs/001.json',dict(step=1,role='W'))
    c.write(tmp_path/'prior/native-intent.json',source)
    monkeypatch.setattr(continuation,'validate_wake_policy',lambda *_:None)
    monkeypatch.setattr(managed_wait,'validate',lambda *_:pytest.fail('Narrow source cannot acquire a waiting grant'))
    with pytest.raises(ValueError,match='special continuation scope'):
        continuation.validate_wake(tmp_path,1,{},tmp_path/'prior')
