"""One final A finishes its real local transport; no models or scientific runs."""
import json
from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, delivery, feedback, native
from test_history_handoff import finish


LAUNCHER = r'''import json,os,pathlib,sys,time
args=sys.argv[1:];prompt=sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
role=os.environ['THIN_HWA_ROLE']
config=json.loads(pathlib.Path(sys.argv[0]).with_suffix('.json').read_text())
(work/'work/native-prompt').write_text(prompt)
with (work/'work/launches').open('a') as stream:stream.write(role+'\n')
thread='12345678-1111-4111-8111-123456789abc'
print(json.dumps(dict(type='thread.started',thread_id=thread)),flush=True)
home=pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME'])
path=home/'sessions'/('rollout-'+thread+'.jsonl')
def live(input_tokens,output_tokens=0):
 path.parent.mkdir(parents=True,exist_ok=True)
 if not path.exists():
  path.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
 with path.open('a') as stream:
  usage=dict(input_tokens=input_tokens,cached_input_tokens=min(100000,input_tokens),output_tokens=output_tokens)
  stream.write(json.dumps(dict(type='event_msg',payload=dict(type='token_count',info=dict(total_token_usage=usage))))+'\n')
if role=='W':
 live(config['worker_tokens'])
 (work/'work/live-ready').touch()
 time.sleep(5)
 raise SystemExit(9)
assert role=='A'
assert 'This is your final scientific return' in prompt
assert 'Explain completed work' in prompt or 'explain what you completed' in prompt
assert 'final_a_return' not in prompt and 'control-ledger' not in prompt
if config.get('live_crossing'):
 live(275001)
 time.sleep(.2)
usage=dict(input_tokens=280900,cached_input_tokens=100000,output_tokens=34)
if config.get('bad_terminal'):usage['input_tokens']=200000
print(json.dumps(dict(type='turn.completed',usage=usage)),flush=True)
(work/'work/terminal-ready').touch()
time.sleep(config.get('delay',.2))
if config.get('missing'):raise SystemExit(0)
payload=dict(summary='Completed the bounded evidence assessment; the objective remains unresolved.',
 scientific_result='Completed: assessed the accepted observations. Unfinished: the missing experiment was not rerun. Gap: the objective lacks a qualifying observation.',
 limitations=['No scientific success is asserted.','A stopped predecessor has no accepted scientific conclusion.'],
 implementation_selection=None,search_review=None,progress_assessment=None,decision_request=None,portfolio_review=None,
 candidate_recommendation=None,next_action=config.get('action','finish'),route_reason='Retain this final report without further scientific dispatch.')
output=pathlib.Path(args[args.index('--output-last-message')+1])
if config.get('unsafe'):
 target=work/'work/unsafe-output';target.write_text(json.dumps(payload));output.symlink_to(target)
else:output.write_text(json.dumps(payload))
if config.get('returncode'):raise SystemExit(config['returncode'])
'''


def instance(tmp_path, *, max_tokens=1_000_000, max_steps=2, mode='soft', route='analyze',
             live_crossing=False, worker_tokens=800_000, final=False, **config):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Assess existing evidence and preserve missing observations.')
    launcher=tmp_path/'native.py';launcher.write_text(LAUNCHER)
    c.write(launcher.with_suffix('.json'),dict(worker_tokens=worker_tokens,live_crossing=live_crossing,**config))
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable,str(launcher)],max_tokens=max_tokens,max_seconds=90,max_steps=max_steps,
        model='deterministic-fixture-no-model',reasoning_effort='high',search_policy=None,node_budget_mode=mode,
        node_budget_policy={role:dict(max_observed_tokens=275000 if role=='A' else 400000 if role=='W' else 10000,
                                     max_seconds=10) for role in 'HWAE'})
    finish(root,1,'H',[],action=route)
    if final:
        budget.begin_closeout(root,1,2,'Explicit final response fixture',final_only=True)
    return root


def accepted(root):
    value=budget.final_a_barrier(root)
    assert value is not None
    step=value['step'];record=c.host_record(c.workspace(root,step))
    return value,step,record


@pytest.mark.parametrize('shared_ceiling',[1_000_000,280_000])
def test_last_a_local_and_shared_token_overrun_waits_for_original_pending(tmp_path,shared_ceiling):
    root=instance(tmp_path,max_tokens=shared_ceiling,final=True)
    original=c.read(root/'settings.json')
    result=controller.run(root)
    assert result['status']=='final_a_completed',json.dumps(result,indent=2)
    event,step,record=accepted(root)
    assert event['basis']=='final_state' and step==2
    outcome=c.read(record/'native-outcome.json')
    assert outcome['returncode']==0 and outcome['stop_reason'] is None
    assert (c.workspace(root,step)/'work/launches').read_text()=='A\n'
    assert c.validate(c.workspace(root,step))['scientific_result'].startswith('Completed:')
    assert c.read(record/'usage.json')==dict(input_tokens=280900,cached_input_tokens=100000,output_tokens=34)
    overrun=outcome['final_a_completion']['final_overrun']
    assert overrun['observed_node_tokens']==280934 and overrun['local_tokens_over_target']==5934
    assert overrun['shared_tokens_over_ceiling']==max(0,280944-shared_ceiling)
    assert result['final_overrun']==overrun
    assert c.read(root/'settings.json')==original and budget.snapshot(root)['max_tokens']==shared_ceiling
    assert result['usage']['observed_tokens']==280944  # Cached input was already included once.
    saved={path:path.read_bytes() for path in (record/'native-intent.json',record/'native-outcome.json',record/'usage.json')}
    assert controller.run(root)['status']=='final_a_completed'
    assert all(path.read_bytes()==raw for path,raw in saved.items())
    assert not (root/'jobs/003.json').exists()


def test_final_a_live_counter_crosses_local_target_then_returns_json(tmp_path):
    root=instance(tmp_path,live_crossing=True,final=True)
    assert controller.run(root)['status']=='final_a_completed'
    _,step,record=accepted(root)
    observation=c.read(record/'usage-observation.json')
    assert observation['coverage']=='terminal' and observation['live_tokens']['input_tokens']>=275001
    hint=c.read(c.workspace(root,step)/'work/budget-status.json')
    assert hint['current_work_boundary']=='final_a_return'
    assert hint['current_work_observed_tokens_remaining_upper_bound'] is None
    assert hint['current_work_active_seconds_available']>0
    assert c.validate(c.workspace(root,step))['limitations'][0]=='No scientific success is asserted.'


@pytest.mark.parametrize('action',['continue_w','revise_h','extract','audit'])
def test_final_a_forbidden_action_retains_raw_without_a_seal_or_followup(tmp_path,action):
    root=instance(tmp_path,route='analyze',action=action,final=True)
    result=controller.run(root)
    assert result['status']=='closeout_native_unsealed',json.dumps(result,indent=2)
    _,step,record=accepted(root)
    assert step==2
    schema=c.read(record/'science-result.schema.json')
    assert schema['properties']['next_action']['enum']==['finish']
    raw=record/'science-output.json';original=raw.read_bytes()
    assert c.read(raw)['next_action']==action
    assert not (record/'result-identity.json').exists()
    assert not (record/'continuations').exists()
    assert controller.run(root)['status']=='closeout_native_unsealed'
    assert raw.read_bytes()==original
    assert (c.workspace(root,2)/'work/launches').read_text()=='A\n'
    assert sorted(path.name for path in (root/'jobs').glob('*.json'))==['001.json','002.json']
    assert not list(root.glob('host-candidate-audits/**/intent.json'))


def test_no_adapter_final_report_runs_before_worker_at_exhausted_shared_balance(tmp_path):
    root=instance(tmp_path,max_tokens=10,max_steps=6,route='experiment')
    settings=(root/'settings.json').read_bytes()
    h_seal=(root/'native-evidence/001/result-identity.json').read_bytes()
    assert native.usage(root)['observed_tokens']==budget.snapshot(root)['max_tokens']==10
    result=controller.run(root)
    assert result['status']=='final_a_completed',json.dumps(result,indent=2)
    event,step,record=accepted(root)
    assert event['basis']=='final_state'
    assert budget.closeout_state(root)['begin']['mode']=='final'
    assert result['submission_result']['status']=='unsubmitted'
    assert event['selection']['stopped_original'] is None and step==2
    assert c.read(root/'jobs/002.json')['role']=='A'
    assert (c.workspace(root,2)/'work/launches').read_text()=='A\n'
    assert not (root/'host-delivery').exists()
    assert not (root/'jobs/003.json').exists()
    assert c.read(record/'science-output.json')['scientific_result'].startswith('Completed:')
    assert (root/'settings.json').read_bytes()==settings
    assert (root/'native-evidence/001/result-identity.json').read_bytes()==h_seal




@pytest.mark.parametrize('config',[dict(returncode=7),dict(missing=True),dict(unsafe=True)])
def test_final_token_permission_does_not_qualify_bad_transport(tmp_path,config):
    root=instance(tmp_path,final=True,**config)
    result=controller.run(root)
    assert result['status']!='final_a_completed',json.dumps(result,indent=2)
    _,_,record=accepted(root)
    assert not (record/'result-identity.json').exists()
    assert not (record/'continuations').exists()
    assert not (root/'jobs/003.json').exists()


def test_final_terminal_billing_mismatch_keeps_live_lower_bound_and_original_report(tmp_path):
    root=instance(tmp_path,final=True,live_crossing=True,bad_terminal=True)
    result=controller.run(root)
    assert result['status']=='final_a_completed',json.dumps(result,indent=2)
    grant,step,record=accepted(root)
    outcome=c.read(record/'native-outcome.json')
    assert outcome['returncode']==0 and outcome['stop_reason'] is None
    assert outcome['cleanup']=='observed_group_cleaned'
    assert outcome['metering']['coverage']=='unknown' and outcome['metering']['source']=='turn.completed'
    assert outcome['metering']['reason']=='terminal_usage_below_live_observation'
    assert c.read(record/'usage.json')['input_tokens']>=275001
    usage=native.usage(root)
    assert usage['unknown_steps']==[step] and usage['observed_tokens']>=275045
    assert outcome['final_a_completion']['final_overrun']['usage_coverage']=='unknown'
    assert c.validate(c.workspace(root,step))['scientific_result'].startswith('Completed:')
    originals={path:path.read_bytes() for path in record.iterdir() if path.is_file()}
    assert controller.run(root)['status']=='final_a_completed'
    assert native.usage(root)==usage and budget.final_a_barrier(root)==grant
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    assert (c.workspace(root,step)/'work/launches').read_text()=='A\n'
    assert not (record/'continuations').exists() and not (root/'jobs/003.json').exists()


def external_scene(tmp_path,monkeypatch):
    import test_closeout_protocol as protocol
    old="pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))\nif item.get('pause'):(root/'pause.request').touch()\nprint(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))),flush=True)"
    replacement="print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=280900,cached_input_tokens=100000,output_tokens=34))),flush=True)\ntime.sleep(.2)\npathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))\nif item.get('pause'):(root/'pause.request').touch()"
    assert old in protocol.NATIVE
    monkeypatch.setattr(protocol,'NATIVE',protocol.NATIVE.replace('import json,os,pathlib,sys','import json,os,pathlib,sys,time').replace(old,replacement))
    root=protocol.scene(tmp_path,[dict(action='deliver',candidate='run-1')],node_budget_mode='soft')
    budget.amend(root,max_tokens=1150,authorization_note='Retain one configured final A and original transport floor.')
    return root


def test_exact_final_delivery_preserves_two_reserved_actions_despite_shared_overrun(tmp_path,monkeypatch):
    root=external_scene(tmp_path,monkeypatch)
    result=controller.closeout(root)
    assert result['status']=='delivery_confirmed',json.dumps(result,indent=2)
    event,step,record=accepted(root)
    assert event['basis']=='final_state' and step==3
    assert c.read(record/'native-outcome.json')['final_a_completion']['final_overrun']['shared_tokens_over_ceiling']>0
    actions=budget.closeout_state(root)['actions']
    assert [row['intent']['action_kind'] for row in actions]==['submit','confirm']
    assert all(row['intent']['final_return']==c.read(record/'native-intent.json')['budget_slice']['final_return'] for row in actions)
    last=actions[-1]['intent']
    with pytest.raises(budget.NodeBudgetUnavailable,match='final_a_delivery_already_confirmed'):
        budget.accept_action(root,'confirm',last['operation_id'],last['decision'],last['binding'],10)
    assert budget.snapshot(root)['max_tokens']==1150 and not (root/'jobs/004.json').exists()
    assert controller.closeout(root)['status']=='delivery_confirmed'
    assert len(budget.closeout_state(root)['actions'])==2


def test_unknown_final_delivery_confirms_same_original_once_per_explicit_run(tmp_path,monkeypatch):
    root=external_scene(tmp_path,monkeypatch)
    calls=[]
    def unknown(root,intent,remaining,**kwargs):
        calls.append((intent['action_kind'],intent['operation_id']))
        return delivery._result(intent['binding'],'unknown','Deterministic unknown receipt; no external call was made.')
    monkeypatch.setattr(delivery,'invoke',unknown)
    assert controller.closeout(root)['status']=='delivery_unknown'
    assert controller.closeout(root)['status']=='delivery_unknown'
    assert controller.closeout(root)['status']=='delivery_unknown'
    assert [kind for kind,_ in calls]==['submit','confirm','confirm']
    assert len({operation for _,operation in calls})==1
    assert len(budget.closeout_state(root)['actions'])==3
    assert not (root/'jobs/004.json').exists()


def final_hint(tmp_path):
    root=instance(tmp_path,final=True)
    job=c.build_job(root,2,'A',[1],final_report=dict(source_cutoff=1,stopped_original=None))
    intent=dict(step=2,task_sha256=c.digest(root/'jobs/002.json'),budget_slice=budget.accept_node(root,job))
    record=c.host_record(c.workspace(root,2))
    meter=dict(input_tokens=280900,cached_input_tokens=100000,output_tokens=34)
    c.write(record/'native-intent.json',intent)
    c.write(record/'usage.json',meter)
    budget.write_node_hint(root,job,intent,budget.snapshot(root),meter,observed=True)
    return c.workspace(root,2)


def test_final_hint_consumer_keeps_token_none_and_finite_time_advice(tmp_path):
    work=final_hint(tmp_path)
    value=feedback.budget_hint(work)
    assert value['status']=='observed_advice'
    assert value['current_work_observed_tokens_remaining_upper_bound'] is None
    assert value['current_work_active_seconds_available']>0
    assert value['current_work_boundary']=='final_a_return'
    assert value['final_response_allowance']=='original_final_a_token_overrun_authorized'
    assert value['review_request']['enforced'] is False
    assert 'Not execution authorization' in value['scope']


@pytest.mark.parametrize('mutation',['missing_token','current_work_boundary','final_response_allowance','target_kind',
                                     'updated_at_unix','shared_observed_tokens_remaining_upper_bound',
                                     'shared_active_seconds_remaining','current_work_active_seconds_available'])
def test_final_hint_none_is_only_valid_for_the_explicit_token_exception(tmp_path,mutation):
    work=final_hint(tmp_path)
    path=work/'work/budget-status.json';hint=c.read(path)
    if mutation=='missing_token':hint.pop('current_work_observed_tokens_remaining_upper_bound')
    elif mutation in {'current_work_boundary','final_response_allowance','target_kind'}:hint.pop(mutation)
    else:hint[mutation]=None
    path.chmod(0o600);c.write(path,hint)
    assert feedback.budget_hint(work)['status']=='unavailable'
