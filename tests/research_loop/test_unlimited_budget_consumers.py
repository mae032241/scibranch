"""Unlimited shared ceilings preserve finite leases, calls and author permits."""
import json
import sys
import threading
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import (
    accepted_budget, author_closeout, budget, configuration_updates, contracts as c,
    controller, continuation, execution, experiment_client, experiments,
    managed_wait, native, role_share, runtime, shared_input_views, storage_policy,
    worker_execution_bridge as bridge,
)


@pytest.mark.process_e2e
@pytest.mark.parametrize('tokens,seconds', [(None, None), (None, 60), (1000, None)])
def test_native_launch_and_status_accept_unlimited_shared_ceilings(tmp_path, tokens, seconds):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Capture a transport request only.')
    launcher=tmp_path/'capture.py'
    launcher.write_text('''import json,os,pathlib,sys
args=sys.argv[1:];work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
(work/'work/prompt.txt').write_text(sys.stdin.read())
pathlib.Path(args[args.index('--output-last-message')+1]).write_text('{}')
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))),flush=True)
''')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable,str(launcher)],
        max_tokens=tokens,max_seconds=seconds,max_steps=4,model='fixture-no-model',
        reasoning_effort='high',search_policy=None,experiment_mode='external',node_budget_mode='soft',
        node_budget_policy={role:dict(max_observed_tokens=100,max_seconds=10) for role in 'HWAE'})
    job=c.build_job(root,1,'H',[])
    intent=dict(step=1,task_ref=c.record_ref(root/'jobs/001.json'),**native.profiles.binding(c.settings(root),job))
    anchor=budget.accept_node(root,job)
    if anchor is not None:intent['budget_slice']=anchor
    accepted_budget.accept_native(root,1,execution.origin(root,1),intent)
    outcome=native.run_native(root,1)
    assert outcome['returncode']==0 and outcome['stop_reason'] is None
    prompt=(c.workspace(root,1)/'work/prompt.txt').read_text()
    status=next(json.loads(line) for line in prompt.splitlines() if line.startswith('{"shared_seconds_remaining"'))
    assert (status['shared_seconds_remaining'] is None)==(seconds is None)
    assert status['shared_observed_tokens_remaining']==tokens
    assert native.usage(root)['observed_tokens']==10


def test_cycle_advice_distinguishes_unlimited_balance_from_unknown_usage(monkeypatch,tmp_path):
    advice=dict(complete_sample_count=2,reported_tokens_sum=40,excluded_sample_count=0,samples=[])
    monkeypatch.setattr(accepted_budget,'read',lambda root:dict(
        search=dict(portfolio=dict(token_advice=advice)),dispatch_count=4))
    meter=dict(accounted_tokens=50,observed_tokens=50,unknown_steps=[],reserved_unknown_steps=[])
    value=native.cycle_token_advice(tmp_path,{},usage_snapshot=meter,budget_snapshot=dict(max_tokens=None))
    assert value['status']=='available' and value['reasons']==[]
    assert value['remaining_reported_tokens'] is None and value['reference_cycles'] is None
    value=native.cycle_token_advice(tmp_path,{},usage_snapshot=dict(meter,blocking_unknown_count=1),
        budget_snapshot=dict(max_tokens=None))
    assert value['reasons']==['usage_unknown']


def review_projection(tmp_path,monkeypatch,*,deadline=None,lease=110):
    stamp=dict(at_boottime=100,at_unix=1000,boot_id='original-boot')
    monkeypatch.setattr(budget,'clock_stamp',lambda:stamp)
    config=dict(admission=dict(version=1,mode='soft',directory=str(tmp_path),stage_anchor='original'),
        scope=dict(step=1),native_attempt=dict(number=0,execution_key='original-attempt'))
    value=dict(scope=config['scope'],stage_anchor='original',native_attempt=config['native_attempt'],
        boot_id=stamp['boot_id'],deadline_boottime=deadline,valid_until_boottime=lease,pending=False,reason=None)
    c.write(tmp_path/'review.json',value)
    return config,value


@pytest.mark.parametrize('deadline,lease,reason', [
    (None,110,None),(None,99,'host_review_observation_expired'),
    (99,110,'review_deadline_reached'),(101,110,None),
])
def test_unlimited_review_deadline_keeps_finite_lease(tmp_path,monkeypatch,deadline,lease,reason):
    config,_=review_projection(tmp_path,monkeypatch,deadline=deadline,lease=lease)
    value=experiments.review_status(config)
    assert value['reason']==reason and value['requested']==bool(reason)
    assert value['enforced']==(reason!='host_review_observation_expired')


@pytest.mark.parametrize('field,value', [
    ('valid_until_boottime',None),('valid_until_boottime',float('inf')),
    ('valid_until_boottime',True),('valid_until_boottime',0),
    ('deadline_boottime',float('inf')),('deadline_boottime',True),('deadline_boottime',0),
])
def test_unlimited_global_time_never_makes_malformed_review_facts_valid(tmp_path,monkeypatch,field,value):
    config,projection=review_projection(tmp_path,monkeypatch)
    projection[field]=value
    (tmp_path/'review.json').write_text(json.dumps(projection))
    with pytest.raises(experiments.IntegrityError):experiments.review_status(config)


def test_unlimited_global_time_still_requires_review_lease(tmp_path,monkeypatch):
    config,projection=review_projection(tmp_path,monkeypatch)
    projection.pop('valid_until_boottime');c.write(tmp_path/'review.json',projection)
    with pytest.raises(experiments.IntegrityError):experiments.review_status(config)


@pytest.mark.parametrize('total,thinking,expected', [
    (None,100_000_000,None),(100,34,None),(100,35,'role_share_thinking_reached'),
])
def test_optional_role_percentages_have_no_quota_on_unlimited_tokens(monkeypatch,tmp_path,total,thinking,expected):
    calls=[]
    monkeypatch.setattr(role_share,'configured',lambda *args:dict(role_share.DEFAULT))
    monkeypatch.setattr(role_share,'usage',lambda root,step:calls.append(step) or dict(thinking=thinking,E=0))
    monkeypatch.setattr(budget,'ceilings',lambda root:dict(max_tokens=total))
    assert role_share.reason(tmp_path,2,dict(role='E'),{},'H')==expected
    assert calls==[2]
    assert role_share.reason(tmp_path,2,dict(role='H'),{},'H')=='role_repeat_without_evidence'


@pytest.mark.parametrize('seconds', [None,.25])
def test_managed_wait_polls_and_wakes_with_nullable_shared_time(tmp_path,monkeypatch,seconds):
    job=dict(step=1,role='W');c.write(tmp_path/'jobs/001.json',job)
    call=tmp_path/'call';call.mkdir();accepted=call/'accepted.json';c.write(accepted,dict(call_id='original'))
    record=tmp_path/'attempt';record.mkdir()
    config=dict(execution_interface=dict(capabilities=dict(query=True)))
    monkeypatch.setattr(execution,'record',lambda *args:record)
    monkeypatch.setattr(managed_wait,'validate',lambda *args:dict(calls=[dict(accepted=dict(path=str(accepted)))]))
    monkeypatch.setattr(managed_wait,'_config',lambda *args:(None,config))
    monkeypatch.setattr(continuation,'validate_wake_policy',lambda *args:None)
    states=iter(['running','succeeded'])
    monkeypatch.setattr(experiments,'load',lambda config:SimpleNamespace(status_details=lambda call:dict(state=next(states))))
    def observe(*args):
        value=dict(status='known_success');c.write(call/'execution-observation.json',value)
        return value
    monkeypatch.setattr(experiments,'record_observation',observe)
    monkeypatch.setattr(experiments,'verify_observation',lambda *args,**kwargs:None)
    snapshots=[];transitions=[];slept=[];woken=[]
    monkeypatch.setattr(budget,'snapshot',lambda root:snapshots.append(root) or dict(seconds_remaining=seconds))
    monkeypatch.setattr(budget,'transition',lambda *args:transitions.append(args))
    monkeypatch.setattr(managed_wait.time,'sleep',slept.append)
    monkeypatch.setattr(continuation,'wake_start',lambda *args:woken.append(args))
    managed_wait.wait_and_wake(tmp_path,1)
    assert len(snapshots)==3 and len(transitions)==1
    assert slept==[managed_wait.POLL_SECONDS if seconds is None else seconds]
    assert len(woken)==1 and len(woken[0][2])==1


def test_wake_packet_uses_finite_io_window_with_unlimited_global_time(tmp_path,monkeypatch):
    monkeypatch.setattr(c,'workspace',lambda *args:tmp_path)
    monkeypatch.setattr(c,'settings',lambda *args:dict(max_seconds=None))
    monkeypatch.setattr(shared_input_views,'dynamic_root',lambda *args:None)
    deadlines=[];original=storage_policy.Deadline
    monkeypatch.setattr(storage_policy,'Deadline',lambda seconds:deadlines.append(seconds) or original(seconds))
    managed_wait.wake_packet(tmp_path,dict(step=1),dict(execution_wake=dict(observations=[])))
    assert deadlines==[65]
    assert c.read(tmp_path/'work/execution-results.json')['executions']==[]


class FundingAccepted(Exception):
    pass


@pytest.mark.parametrize('successor', [False,True])
@pytest.mark.parametrize('tokens,seconds,allowed', [(None,None,True),(None,10,True),(151,None,True),(150,None,False),(None,9,False)])
def test_author_funding_accepts_unlimited_shared_dimensions_but_keeps_finite_reserves(
        tmp_path,monkeypatch,successor,tokens,seconds,allowed):
    job=dict(step=1,role='W');c.write(tmp_path/'jobs/001.json',job)
    origin=tmp_path/'attempt';origin.mkdir()
    source=origin/'continuations/001' if successor else origin;source.mkdir(parents=True,exist_ok=True)
    c.write(source/'native-intent.json',{})
    binding=c.record_ref(source/'native-intent.json') if successor else c.record_ref(tmp_path/'jobs/001.json')
    stamp=dict(boot_id='original-boot')
    for name in ('runner-process.json','native-process.json'):
        c.write(source/name,dict(boot_id=stamp['boot_id'],task_ref=binding))
    c.write(origin/'native-outcome.json',dict(stop_reason='token_limit',cleanup='observed_group_cleaned'))
    monkeypatch.setattr(configuration_updates,'for_step',lambda *args:{})
    monkeypatch.setattr(c,'read_job',lambda *args:job)
    monkeypatch.setattr(execution,'origin',lambda *args:origin)
    monkeypatch.setattr(execution,'records',lambda *args:[origin,source])
    monkeypatch.setattr(runtime,'process_matches',lambda *args:False)
    monkeypatch.setattr(budget,'clock_stamp',lambda:stamp)
    monkeypatch.setattr(budget,'closeout_state',lambda *args:dict(begin=None))
    monkeypatch.setattr(budget,'execution_blocked',lambda *args:False)
    monkeypatch.setattr(budget,'snapshot',lambda *args:dict(max_tokens=tokens,seconds_remaining=seconds))
    monkeypatch.setattr(budget,'delivery_reserve',lambda *args:dict(tokens=50,seconds=5,steps=1))
    monkeypatch.setattr(budget,'effective_max_steps',lambda *args:5)
    monkeypatch.setattr(budget,'used_steps',lambda *args:1)
    monkeypatch.setattr(native,'usage',lambda *args:dict(accounted_tokens=100,observed_tokens=100))
    def accepted(*args):raise FundingAccepted()
    if successor:
        path=author_closeout.successor_path(tmp_path,1)
        c.write(path,dict(max_tokens=10,max_seconds=5,authorization_note='bounded',authorization_statement='original authorization'))
        monkeypatch.setattr(author_closeout,'validate_successor',lambda root,value:value)
        monkeypatch.setattr(author_closeout,'require_successor_eligibility',accepted)
        action=lambda:author_closeout.start_successor(tmp_path,1,max_tokens=10,max_seconds=5,
            authorization_note='bounded',authorization_statement='original authorization')
    else:
        monkeypatch.setattr(author_closeout,'terminal_facts',accepted)
        action=lambda:author_closeout.start(tmp_path,1,max_tokens=10,max_seconds=5,authorization_note='bounded')
    with pytest.raises(FundingAccepted if allowed else ValueError,match=None if allowed else 'exceed remaining authorization'):
        action()


@pytest.mark.parametrize('seconds,expected', [(None,None),(2,102),(0,100)])
def test_bridge_deadline_preserves_unlimited_or_finite_original_permission(tmp_path,monkeypatch,seconds,expected):
    c.write(tmp_path/'jobs/001.json',dict(step=1));c.write(tmp_path/'attempt/native-intent.json',{})
    monkeypatch.setattr(execution,'record',lambda *args:tmp_path/'attempt')
    monkeypatch.setattr(budget,'snapshot',lambda *args,**kwargs:{})
    monkeypatch.setattr(budget,'execution_available',lambda *args:dict(seconds=seconds))
    monkeypatch.setattr(budget,'node_mode',lambda *args:'soft')
    monkeypatch.setattr(native,'usage',lambda *args:{})
    monkeypatch.setattr(bridge.time,'clock_gettime',lambda clock:100)
    assert bridge._bridge_deadline(tmp_path,dict(scope=dict(step=1),native_attempt=dict(number=0)))==expected


@pytest.mark.parametrize('shared_deadline,call_seconds,expected', [(None,65,173),(None,900,1008),(120,65,120),(500,65,173)])
@pytest.mark.parametrize('execution_authority', [False,True])
def test_bridge_operations_keep_finite_call_window_with_nullable_global_time(
        tmp_path,monkeypatch,shared_deadline,call_seconds,expected,execution_authority):
    config=dict(max_seconds=call_seconds,scope=dict(step=1),native_attempt=dict(number=0),
        execution_interface=dict(config_record='original'))
    accepted=dict(call_id='original-call',scope=config['scope'],native_attempt=config['native_attempt'],
        interface=config['execution_interface'],config_record='original',execution_authority=execution_authority)
    monkeypatch.setattr(bridge,'_configuration',lambda server:config)
    monkeypatch.setattr(bridge,'_arguments',lambda arguments,*args:arguments)
    monkeypatch.setattr(bridge,'_bridge_deadline',lambda *args:shared_deadline)
    monkeypatch.setattr(experiment_client,'root_for_configuration',lambda *args:tmp_path)
    monkeypatch.setattr(experiments,'Records',lambda *args:SimpleNamespace(accepted=lambda call:accepted))
    monkeypatch.setattr(bridge.time,'clock_gettime',lambda clock:100)
    markers=[]
    def launch(body,*args):
        markers.append(c.read(tmp_path/'bridge-operation.json'))
        assert body['max_seconds']==call_seconds
        return 'completed'
    server=SimpleNamespace(work=tmp_path,record=tmp_path,stop=threading.Event(),
        launch=launch,environment={},config_path=tmp_path/'config.json')
    def main(arguments):
        assert c.read(tmp_path/'bridge-operation.json')['deadline_boottime']==expected
        assert bridge.local_process_launch({key:value for key,value in config.items() if key!='execution_interface'},
            accepted,['fixture'],tmp_path)=='completed'
        print(json.dumps(dict(status='known_success')))
        return 0
    monkeypatch.setattr(experiments,'main',main)
    assert bridge._execute(server,['run'])['code']==0
    assert markers[0]['deadline_boottime']==expected
