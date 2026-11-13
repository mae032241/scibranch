"""One final A finishes its real local transport; no models or scientific runs."""
import json
from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, delivery, feedback, native
from test_history_handoff import finish


LAUNCHER = r'''import hashlib,json,os,pathlib,sys,time
args=sys.argv[1:];prompt=sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
role=os.environ['THIN_HWA_ROLE']
task=json.loads(pathlib.Path(os.environ['THIN_HWA_JOB_MANIFEST']).read_text())
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
assert 'Protected FINAL:' in prompt and 'no new role/audit/experiment' in prompt
assert 'final_a_return' not in prompt and 'control-ledger' not in prompt
if config.get('live_crossing'):
 live(275001)
 time.sleep(.2)
payload=dict(summary='Completed the bounded evidence assessment; the objective remains unresolved.',
 scientific_result='Completed: assessed the accepted observations. Unfinished: the missing experiment was not rerun. Gap: the objective lacks a qualifying observation.',
 limitations=['No scientific success is asserted.','A stopped predecessor has no accepted scientific conclusion.'],
 implementation_selection=None,search_review=None,progress_assessment=None,decision_request=None,portfolio_review=None,
 candidate_recommendation=None,next_action=config.get('action','finish'),route_reason='Retain this final report without further scientific dispatch.')
payload['final_coverage']=[dict(mechanism_ref=row['mechanism_ref'],status='unexplored',
 reason='Deterministic transport fixture has no scientific exploration evidence.')
 for row in task.get('final_coverage_mechanisms',[])] or None
raw=json.dumps(payload)
binding=dict(sha256=hashlib.sha256(raw.encode()).hexdigest(),size_bytes=len(raw.encode()))
print(json.dumps(dict(type='item.completed',item=dict(type='agent_message',text=raw,phase='final_answer',
 native_final=True,final_binding=binding))),flush=True)
usage=dict(input_tokens=280900,cached_input_tokens=100000,output_tokens=34)
if config.get('bad_terminal'):usage['input_tokens']=200000
print(json.dumps(dict(type='turn.completed',usage=usage)),flush=True)
(work/'work/terminal-ready').touch()
time.sleep(config.get('delay',.2))
if config.get('missing'):raise SystemExit(0)
output=pathlib.Path(args[args.index('--output-last-message')+1])
if config.get('unsafe'):
 target=work/'work/unsafe-output';target.write_text(raw);output.symlink_to(target)
else:output.write_text(raw)
if config.get('returncode'):raise SystemExit(config['returncode'])
'''


def instance(tmp_path, *, max_tokens=1_000_000, max_steps=2, max_seconds=90, mode='soft', route='analyze',
             live_crossing=False, worker_tokens=800_000, final=False, **config):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Assess existing evidence and preserve missing observations.')
    launcher=tmp_path/'native.py';launcher.write_text(LAUNCHER)
    c.write(launcher.with_suffix('.json'),dict(worker_tokens=worker_tokens,live_crossing=live_crossing,**config))
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable,str(launcher)],max_tokens=max_tokens,max_seconds=max_seconds,max_steps=max_steps,
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
@pytest.mark.process_e2e
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


@pytest.mark.parametrize('terminal_input_tokens',[280900,200000])
@pytest.mark.process_e2e
def test_raw_session_counter_is_not_live_evidence_and_terminal_usage_is_retained(tmp_path,terminal_input_tokens):
    # The launcher writes opaque rollout token_count bytes, without a hook receipt.
    root=instance(tmp_path,live_crossing=True,final=True,bad_terminal=terminal_input_tokens==200000)
    result=controller.run(root)
    assert result['status']=='final_a_completed',json.dumps(result,indent=2)
    grant,step,record=accepted(root)
    outcome=c.read(record/'native-outcome.json')
    assert outcome['returncode']==0 and outcome['stop_reason'] is None
    assert outcome['cleanup']=='observed_group_cleaned'
    observation=c.read(record/'usage-observation.json')
    assert observation['live_coverage']=='unknown' and observation['live_reason']=='token_counter_unreported'
    assert observation['has_observation'] is False and observation['session_binding'] is None
    assert observation['context_windows']['coverage']=='unknown'
    assert observation['coverage']=='terminal' and observation['source']=='turn.completed'
    assert observation['reconciliation']=='terminal_fallback'
    meter=dict(input_tokens=terminal_input_tokens,cached_input_tokens=100000,output_tokens=34)
    assert observation['tokens']==c.read(record/'usage.json')==meter
    usage=native.usage(root)
    assert usage['unknown_steps']==[] and usage['observed_tokens']==terminal_input_tokens+44
    overrun=outcome['final_a_completion']['final_overrun']
    assert overrun['observed_node_tokens']==meter['input_tokens']+meter['output_tokens']
    assert overrun['accounted_shared_tokens']==usage['accounted_tokens']
    assert 'usage_coverage' not in overrun
    hint=c.read(c.workspace(root,step)/'work/budget-status.json')
    assert hint['current_work_boundary']=='final_a_return'
    assert hint['current_work_observed_tokens_remaining_upper_bound'] is None
    assert hint['current_work_active_seconds_available']>0
    assert c.validate(c.workspace(root,step))['limitations'][0]=='No scientific success is asserted.'
    originals={path:path.read_bytes() for path in record.iterdir() if path.is_file()}
    assert controller.run(root)['status']=='final_a_completed'
    assert native.usage(root)==usage and budget.final_a_barrier(root)==grant
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    assert (c.workspace(root,step)/'work/launches').read_text()=='A\n'
    assert not (record/'continuations').exists() and not (root/'jobs/003.json').exists()


@pytest.mark.parametrize('action',['continue_w','revise_h','extract','audit'])
@pytest.mark.process_e2e
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


@pytest.mark.process_e2e
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
@pytest.mark.process_e2e
def test_final_token_permission_does_not_qualify_bad_transport(tmp_path,config):
    root=instance(tmp_path,final=True,**config)
    result=controller.run(root)
    assert result['status']!='final_a_completed',json.dumps(result,indent=2)
    _,_,record=accepted(root)
    assert not (record/'result-identity.json').exists()
    assert not (record/'continuations').exists()
    assert not (root/'jobs/003.json').exists()


def external_scene(tmp_path,monkeypatch,*,action='deliver'):
    import test_closeout_protocol as protocol
    old="pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))\nif item.get('pause'):(root/'pause.request').touch()\nprint(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))),flush=True)"
    replacement="print(json.dumps(dict(type='item.completed',item=dict(type='agent_message',phase='final_answer',text=json.dumps(payload)))),flush=True)\nprint(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=280900,cached_input_tokens=100000,output_tokens=34))),flush=True)\ntime.sleep(.2)\npathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))\nif item.get('pause'):(root/'pause.request').touch()"
    assert old in protocol.NATIVE
    monkeypatch.setattr(protocol,'NATIVE',protocol.NATIVE.replace('import json,os,pathlib,sys','import json,os,pathlib,sys,time').replace(old,replacement))
    root=protocol.scene(tmp_path,[dict(action=action,candidate='run-1' if action=='deliver' else None)],node_budget_mode='soft')
    budget.amend(root,max_tokens=1150,authorization_note='Retain one configured final A and original transport floor.')
    return root


@pytest.mark.process_e2e
def test_exact_final_delivery_preserves_two_reserved_actions_despite_shared_overrun(tmp_path,monkeypatch):
    root=external_scene(tmp_path,monkeypatch)
    settings=(root/'settings.json').read_bytes()
    result=controller.closeout(root)
    assert result['status']=='delivery_confirmed',json.dumps(result,indent=2)
    event,step,record=accepted(root)
    outcome=c.read(record/'native-outcome.json')
    assert outcome['returncode']==0 and outcome['stop_reason'] is None
    assert c.validate(c.workspace(root,step))['scientific_result'].startswith('Exact public evidence')
    assert event['basis']=='final_state' and step==3
    assert c.read(record/'native-outcome.json')['final_a_completion']['final_overrun']['shared_tokens_over_ceiling']>0
    actions=budget.closeout_state(root)['actions']
    assert [row['intent']['action_kind'] for row in actions]==['submit','confirm']
    assert all(row['intent']['final_return']==c.read(record/'native-intent.json')['budget_slice']['final_return'] for row in actions)
    last=actions[-1]['intent']
    with pytest.raises(budget.NodeBudgetUnavailable,match='final_a_delivery_already_confirmed'):
        budget.accept_action(root,'confirm',last['operation_id'],last['decision'],last['binding'],10)
    assert budget.snapshot(root)['max_tokens']==1150 and not (root/'jobs/004.json').exists()
    originals={path:path.read_bytes() for path in (record/'native-intent.json',record/'native-outcome.json',record/'result-identity.json')}
    assert controller.closeout(root)['status']=='delivery_confirmed'
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    assert (root/'settings.json').read_bytes()==settings
    assert len(budget.closeout_state(root)['actions'])==2


@pytest.mark.process_e2e
def test_unknown_final_delivery_confirms_same_original_once_per_explicit_run(tmp_path,monkeypatch):
    root=external_scene(tmp_path,monkeypatch)
    calls=[]
    def unknown(root,intent,remaining,**kwargs):
        calls.append((intent['action_kind'],intent['operation_id']))
        return delivery._result(intent['binding'],'unknown','Deterministic unknown receipt; no external call was made.')
    monkeypatch.setattr(delivery,'invoke',unknown)
    original=None
    for number in range(1,4):
        assert controller.closeout(root)['status']=='delivery_unknown'
        actions=budget.closeout_state(root)['actions']
        if original is None:original=actions[0]
        assert len(actions)==min(number,2) and actions[0]==original
        assert all(row['intent']['operation_id']==original['intent']['operation_id']
            and row['intent']['binding']==original['intent']['binding']
            and row['intent']['decision']==original['intent']['decision']
            and row['outcome']['result']['status']=='unknown' for row in actions)
    assert [kind for kind,_ in calls]==['submit','confirm','confirm']
    assert len({operation for _,operation in calls})==1
    assert len(budget.closeout_state(root)['actions'])==2
    assert not (root/'jobs/004.json').exists()


@pytest.mark.process_e2e
def test_final_a_can_decline_every_available_candidate_without_submission(tmp_path,monkeypatch):
    root=external_scene(tmp_path,monkeypatch,action='finish')
    result=controller.closeout(root)
    diagnostics=dict(result=result)
    if result['status']!='final_a_completed':
        record=c.host_record(c.workspace(root,3))
        for name in ('native-outcome.json','usage-observation.json','science-output.json',
                'science-result.schema.json'):
            path=record/name
            diagnostics[name]=path.read_text() if path.is_file() else 'missing'
    assert result['status']=='final_a_completed',json.dumps(diagnostics,indent=2)
    _,step,record=accepted(root)
    assert step==3 and c.validate(c.workspace(root,step))['next_action']=='finish'
    assert c.validate(c.workspace(root,step))['candidate_recommendation'] is None
    assert result['submission_result']['status']=='unsubmitted'
    assert budget.closeout_state(root)['actions']==[]
    assert not (root/'runs/submitted.json').exists() and not (root/'jobs/004.json').exists()
    original=(record/'result-identity.json').read_bytes()
    assert controller.closeout(root)['status']=='final_a_completed'
    assert (record/'result-identity.json').read_bytes()==original


def node_hint_facts(root,job,intent,meter,*,observed):
    """Supply the advice producer's one accepted cut, as the native owner does."""
    from scidiscovery.research_loop import accepted_budget
    from scidiscovery.research_loop.protocol import stage_scope
    head=accepted_budget.read(root);stamp=budget.clock_stamp()
    record=c.host_record(c.workspace(root,job['step']))
    current=head['current_native']
    live=(None if current is None else dict(record=record.relative_to(root).as_posix(),
        meter=meter if observed else None,metering=None,outcome_present=False,interrupted=False))
    shared=accepted_budget.usage_from_facts(head,job_rows=accepted_budget.load_usage_rows(head),current_native=live)
    limits=budget._snapshot(root,head.base,accepted_budget.AcceptedEvents(head),sample_stamp=stamp)
    return limits,dict(settings=c.settings(root,job),head=head,shared=shared,stamp=stamp,
        task=intent['task_ref'],scope=stage_scope(root,job),
        native_attempt=dict(number=0,execution_key=intent['task_ref']),
        research_investment=None,final_return=budget.final_a_return(root,job,intent),
        closeout_preparation_seconds=None)


def final_hint(tmp_path):
    root=instance(tmp_path,final=True)
    job=c.build_job(root,2,'A',[1],final_report=dict(source_cutoff=1,stopped_original=None))
    intent=dict(step=2,task_ref=c.record_ref(root/'jobs/002.json'),budget_slice=budget.accept_node(root,job))
    record=c.host_record(c.workspace(root,2))
    meter=dict(input_tokens=280900,cached_input_tokens=100000,output_tokens=34)
    c.write(record/'native-intent.json',intent)
    accepted_budget.accept_native(root,2,record,intent)
    c.write(record/'usage.json',meter)
    limits,facts=node_hint_facts(root,job,intent,meter,observed=True)
    budget.write_node_hint(root,job,intent,limits,meter,facts=facts,observed=True)
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
