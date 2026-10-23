"""Same-session native continuation through real detached local processes; no models."""
import json
import sys
import time
from pathlib import Path

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, execution, native, continuation
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from scidiscovery.research_loop.cli import status


FAKE = r'''import json,os,pathlib,sys
args=sys.argv[1:];sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);home=pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME'])
role=os.environ['THIN_HWA_ROLE']
thread='11111111-2222-4333-8444-555555555555'
with (work/'work/launches').open('a') as f:f.write('resume\n' if 'resume' in args else 'exec\n')
print(json.dumps({'type':'thread.started','thread_id':thread}),flush=True)
if 'resume' not in args and role!='A':
 p=home/'sessions/rollout.jsonl';p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps({'type':'session_meta','payload':{'id':thread,'cwd':str(work)}})+'\n')
 print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':3,'output_tokens':4}}),flush=True)
 (work/'work/checkpoint').write_text('original checkpoint')
 sys.exit(7)
if 'resume' in args:assert thread in args and (work/'work/checkpoint').read_text()=='original checkpoint'
assert pathlib.Path(os.environ['CODEX_HOME'])==home
payload={'summary':'Continued scientific finding','scientific_result':'Original checkpoint was retained.','limitations':['Fake transport only'],'implementation_selection':None}
if role=='W':
 payload.update(mechanism_findings=None,stage_intent='review')
 task=json.loads((work/'science-task.json').read_text());assigned=task.get('lineage',{}).get('mechanisms',{})
 nominations=task.get('candidate_nomination_mechanisms',[])
 if nominations:
  payload['candidate_nominations']=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],reason='This transport fixture produces no scientifically suitable candidate and preserves the retained pool for A.') for row in nominations if row['status']!='waived'])
 if assigned.get('task_action')=='screen' or 'Screen the assigned H combination' in task.get('question',''):
  payload['mechanism_findings']=[dict(mechanism=row['mechanism_ref'],basis='blocked',finding='The checkpoint transport fixture has no scientific measurement for this assigned mechanism.') for row in nominations if row['status']=='pending']
if role=='H':payload.update(search_plan=None,next_action='wait',route_reason='Bounded contribution complete')
if role=='A':payload.update(search_review=None,candidate_recommendation=None,progress_assessment=None,decision_request=None,portfolio_review=None,next_action='finish',route_reason='Independent fake conclusion')
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':20,'cached_input_tokens':5,'output_tokens':2}}),flush=True)
if (work/'work/fail-again').exists():sys.exit(7)
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
'''


REPAIR_FAKE = r'''import json,os,pathlib,sys
args=sys.argv[1:];prompt=sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);home=pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME'])
role=os.environ['THIN_HWA_ROLE'];thread='11111111-2222-4333-8444-555555555555'
resume='resume' in args
with (work/'work/launches').open('a') as f:f.write('resume\n' if resume else 'exec\n')
print(json.dumps({'type':'thread.started','thread_id':thread}),flush=True)
if not resume:
 p=home/'sessions/rollout.jsonl';p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps({'type':'session_meta','payload':{'id':thread,'cwd':str(work)}})+'\n')
 (work/'work/completed-experiment').write_text('one completed execution')
else:
 assert thread in args and pathlib.Path(os.environ['CODEX_HOME'])==home
 assert (work/'work/completed-experiment').read_text()=='one completed execution'
 assert 'Repair only your final response' in prompt and 'do not rerun or resubmit' in prompt
 (work/'work/repair-prompt').write_text(prompt)
kind=(work/'work/output-kind').read_text()
if kind!='unknown':print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':3,'output_tokens':4}}),flush=True)
payload={'summary':'Retained finding','scientific_result':'Completed experiment retained.','limitations':[],'implementation_selection':None}
if role=='W':payload.update(mechanism_findings=None,stage_intent='review')
if role=='H':payload.update(search_plan=None,next_action='wait',route_reason='Bounded output repaired')
if role=='A':payload.update(search_review=None,candidate_recommendation=None,progress_assessment=None,decision_request=None,portfolio_review=None,next_action='finish',route_reason='Bounded output repaired')
destination=pathlib.Path(args[args.index('--output-last-message')+1])
if resume and (not (work/'work/repeat-bad-output').exists() or
 (work/'work/valid-after-second').exists() and (work/'work/launches').read_text().count('resume\n')>=2):
 destination.write_text(json.dumps(payload));sys.exit(0)
if kind=='missing':sys.exit(0)
if kind=='unsafe':destination.symlink_to(work/'work/completed-experiment');sys.exit(0)
if kind=='badjson':destination.write_text('{invalid JSON');sys.exit(0)
if kind=='missingfield':del payload['summary']
if kind=='wrongtype':payload['limitations']='not an array'
if kind=='empty':payload['scientific_result']='  '
destination.write_text(json.dumps(payload))
'''


def instance(tmp_path, *, cap=100, attempts=2, max_tokens=1000, steps=6, search_policy=None, delivery_policy=None,
             fake=FAKE, output_repair=False, with_policy=True):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Continue a bounded scientific task using its checkpoint.')
    launcher=tmp_path/'native.py';launcher.write_text(fake)
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable,str(launcher)],max_tokens=max_tokens,
        max_seconds=120,max_steps=steps,model='no-model',reasoning_effort='high', node_budget_mode='soft',search_policy=search_policy,delivery_policy=delivery_policy,
        node_budget_policy={role:dict(max_observed_tokens=cap,max_seconds=20) for role in 'HWAE'},
        resume_policy=dict(max_attempts=attempts,protocol='codex_exec_resume',usage_scope='turn',
                           **(dict(output_repair=True) if output_repair else {})) if with_policy else None)
    return root


def wait_runner(root, step=1):
    # Terminal outcome precedes runner exit by a bounded transport gap.
    from scidiscovery.research_loop import runtime
    end=time.monotonic()+3
    record=execution.record(root,step)
    while runtime.process_matches(c.read(record/'runner-process.json'),execution.identity(root,step)):
        assert time.monotonic()<end
        time.sleep(.02)
    return record


def fail(root, step=1):
    assert controller.run(root)['status']=='native_stopped'
    return wait_runner(root, step)


def repair_instance(tmp_path, role='H', kind='badjson', *, enabled=True, attempts=2, cap=100, max_tokens=1000):
    root=instance(tmp_path, fake=REPAIR_FAKE, output_repair=enabled, attempts=attempts, cap=cap,max_tokens=max_tokens)
    c.build_job(root,1,role,[])
    (c.workspace(root,1)/'work').mkdir(exist_ok=True)
    (c.workspace(root,1)/'work/output-kind').write_text(kind)
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'advance','fake_output_repair')
    return root


@pytest.mark.parametrize('role', list('HWAE'))
@pytest.mark.parametrize('kind', ['badjson','missingfield'])
def test_completed_output_repair_is_same_role_node_session_without_experiment_replay(tmp_path,role,kind):
    root=repair_instance(tmp_path,role,kind)
    assert native.execute_native(root,1)['role']==role
    first=execution.origin(root,1);current=execution.record(root,1)
    assert current==first/'continuations/001'
    assert c.read(first/'native-outcome.json')['returncode']==0
    assert (first/'science-output.json').read_bytes()!=execution.raw(root,1).read_bytes()
    intent=c.read(current/'native-intent.json')
    assert intent['output_repair']['raw_ref']==c.file_ref(first/'science-output.json')
    assert intent['session']['workspace']==str(c.workspace(root,1))
    assert intent['session']['home']==str(root/'native-homes/001')
    assert intent['session']['thread_id']=='11111111-2222-4333-8444-555555555555'
    assert native.usage(root)['observed_tokens']==28 and budget.used_steps(root)==2
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\nresume\n'
    before={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    assert continuation.seal_or_repair(root,1)['role']==role
    assert all(p.read_bytes()==data for p,data in before.items())
    assert (c.workspace(root,1)/'work/completed-experiment').read_text()=='one completed execution'


@pytest.mark.parametrize('kind', ['missing','wrongtype','empty'])
def test_known_output_failure_keeps_execution_facts_and_repairs(tmp_path,kind):
    root=repair_instance(tmp_path,kind=kind)
    assert native.execute_native(root,1)['next_action']=='wait'
    first=execution.origin(root,1);outcome=c.read(first/'native-outcome.json')
    assert outcome['returncode']==0 and outcome['cleanup']=='observed_group_cleaned'
    assert outcome['budget_slice']['usage_observed'] and outcome['thread_id']
    if kind=='missing':
        assert outcome['output_status']=='missing' and not (first/'science-output.json').exists()
    else:
        assert outcome['raw_ref']==c.file_ref(first/'science-output.json')


def test_old_resume_policy_does_not_automatically_repair_successful_bad_output(tmp_path):
    root=repair_instance(tmp_path,enabled=False)
    with pytest.raises(OutputRepairRequired):native.execute_native(root,1)
    wait_runner(root)
    with pytest.raises(OutputRepairRequired):controller.worker_resume(root,1)
    assert len(execution.records(root,1))==1


def test_default_without_resume_policy_retains_typed_output_error(tmp_path,monkeypatch):
    root=instance(tmp_path,fake=REPAIR_FAKE,with_policy=False)
    c.build_job(root,1,'H',[])
    (c.workspace(root,1)/'work').mkdir(exist_ok=True)
    (c.workspace(root,1)/'work/output-kind').write_text('badjson')
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'advance','fake_default_output')
    with pytest.raises(OutputRepairRequired):native.execute_native(root,1)
    wait_runner(root)
    original=c.settings
    monkeypatch.setattr(c,'settings',lambda value,job=None:dict(original(value,job),resume_policy=None))
    with pytest.raises(OutputRepairRequired):continuation.seal_or_repair(root,1)
    assert len(execution.records(root,1))==1


def test_output_repair_shares_original_attempt_limit_and_retains_all_bad_outputs(tmp_path):
    root=repair_instance(tmp_path,attempts=2)
    (c.workspace(root,1)/'work/repeat-bad-output').touch()
    with pytest.raises(continuation.ResumeUnavailable,match='worker_resume_limit'):
        native.execute_native(root,1)
    wait_runner(root)
    paths=execution.records(root,1)
    assert len(paths)==3 and native.usage(root)['observed_tokens']==42
    assert all((p/'science-output.json').read_text()=='{invalid JSON' for p in paths)
    with pytest.raises(continuation.ResumeUnavailable,match='worker_resume_limit'):
        continuation.seal_or_repair(root,1)
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\nresume\nresume\n'


def test_output_repair_does_not_replenish_insufficient_shared_budget(tmp_path,monkeypatch):
    root=repair_instance(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(continuation,'seal_or_repair',c.seal_result)
        with pytest.raises(OutputRepairRequired):native.execute_native(root,1)
    wait_runner(root)
    budget.amend(root,max_tokens=native.usage(root)['accounted_tokens'],authorization_note='Explicit fixture reduction to actual consumed usage')
    with pytest.raises(budget.NodeBudgetUnavailable):continuation.seal_or_repair(root,1)
    assert len(execution.records(root,1))==1 and native.usage(root)['observed_tokens']==14
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'


@pytest.mark.parametrize('attempts', [1,2])
def test_terminal_repair_rechecks_initial_bad_output_before_seal(tmp_path,monkeypatch,attempts):
    root=repair_instance(tmp_path)
    first=execution.origin(root,1)
    if attempts==2:(c.workspace(root,1)/'work/repeat-bad-output').touch()
    original=c.seal_result
    class DeferSeal(RuntimeError):pass
    def terminal_before_seal(root,step):
        current=execution.record(root,step)
        if current!=first:
            intent=c.read(current/'native-intent.json')
            if intent['attempt']==attempts:
                assert c.read(current/'native-outcome.json')['returncode']==0
                raise DeferSeal('Known repaired turn retained before seal')
        return original(root,step)
    if attempts==2:
        # First repair is another bad response; the second is valid.
        # Configure the already frozen fake behavior through a workspace flag.
        (c.workspace(root,1)/'work/valid-after-second').touch()
    with monkeypatch.context() as patch:
        patch.setattr(c,'seal_result',terminal_before_seal)
        with pytest.raises(DeferSeal):native.execute_native(root,1)
    wait_runner(root)
    assert len(execution.records(root,1))==attempts+1
    (first/'science-output.json').write_text('{changed original bad output')
    with pytest.raises(ValueError,match='Original final output changed'):
        c.seal_result(root,1)
    assert not (first/'result-identity.json').exists()
    assert len(execution.records(root,1))==attempts+1


def test_output_repair_without_original_session_stops_without_fresh_fallback(tmp_path,monkeypatch):
    root=repair_instance(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(continuation,'seal_or_repair',c.seal_result)
        with pytest.raises(OutputRepairRequired):native.execute_native(root,1)
    wait_runner(root)
    (root/'native-homes/001/sessions/rollout.jsonl').unlink()
    with pytest.raises(continuation.ResumeUnavailable,match='worker_resume_session_unavailable'):
        continuation.seal_or_repair(root,1)
    assert len(execution.records(root,1))==1
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'


@pytest.mark.parametrize('kind', ['unknown','unsafe'])
def test_unknown_execution_or_unsafe_output_never_starts_repair(tmp_path,kind):
    root=repair_instance(tmp_path,kind=kind)
    with pytest.raises((ValueError,RuntimeError)):native.execute_native(root,1)
    wait_runner(root)
    assert len(execution.records(root,1))==1
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'


@pytest.mark.parametrize('mutation', ['raw','job','schema'])
def test_output_repair_does_not_soften_original_integrity_failure(tmp_path,monkeypatch,mutation):
    root=repair_instance(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(continuation,'seal_or_repair',c.seal_result)
        with pytest.raises(OutputRepairRequired):native.execute_native(root,1)
    first=wait_runner(root)
    if mutation=='raw':(first/'science-output.json').write_text('{different broken output')
    elif mutation=='job':
        job=c.read(root/'jobs/001.json');job['instruction']='changed';c.write(root/'jobs/001.json',job)
    else:c.write(first/'science-result.schema.json',dict(type='object'))
    with pytest.raises(ValueError):continuation.seal_or_repair(root,1)
    assert len(execution.records(root,1))==1


def test_host_write_failure_retries_original_seal_without_model_reexecution(tmp_path,monkeypatch):
    root=repair_instance(tmp_path,kind='valid')
    original=c.write
    def fail_result(path,value):
        if path.name=='result.json':raise OSError('Host disk fixture')
        return original(path,value)
    with monkeypatch.context() as patch:
        patch.setattr(c,'write',fail_result)
        with pytest.raises(OSError,match='Host disk fixture'):native.execute_native(root,1)
    first=wait_runner(root);preserved=(first/'science-output.json').read_bytes()
    assert continuation.seal_or_repair(root,1)['next_action']=='wait'
    assert (first/'science-output.json').read_bytes()==preserved
    assert len(execution.records(root,1))==1 and native.usage(root)['observed_tokens']==14


def test_known_failure_same_thread_success_keeps_originals_and_cumulative_budget(tmp_path):
    root=instance(tmp_path);first=fail(root)
    preserved={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    assert controller.worker_resume(root,1)['status']=='wait'
    assert all(p.read_bytes()==data for p,data in preserved.items())
    assert execution.raw(root,1)==first/'continuations/001/science-output.json'
    assert c.validate(c.workspace(root,1))['summary']=='Continued scientific finding'
    assert native.usage(root)['observed_tokens']==36
    assert budget.used_steps(root)==2
    node=status(root)['nodes'][0]
    assert node['continuations']==1 and node['budget_report']['observed_tokens']==36
    assert node['budget_report']['dispatches']==2
    assert c.read(first/'result-identity.json')['execution_ref']==execution.reference(root,1)
    c.build_job(root,2,'H',[1])
    from scidiscovery.research_loop import history
    assert history.direct_sources(root,c.read(root/'jobs/002.json'))[0][1]['summary']=='Continued scientific finding'
    assert controller.run(root)['status']=='wait'
    assert controller.worker_resume(root,1)['status']=='wait'
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\nresume\n'


@pytest.mark.parametrize('mutation', ['root_intent', 'process_receipt', 'session_header', 'schema', 'task'])
def test_original_binding_tamper_never_creates_continuation(tmp_path, mutation):
    root=instance(tmp_path);first=fail(root)
    if mutation=='root_intent':
        value=c.read(first/'native-intent.json');value['task_ref']=dict(path='/wrong/original.json')
        c.write(first/'native-intent.json',value)
    elif mutation=='process_receipt':
        value=c.read(first/'native-process.json');value['start_ticks']+=1
        c.write(first/'native-process.json',value)
    elif mutation=='session_header':
        p=next((root/'native-homes/001/sessions').glob('*.jsonl'))
        value=json.loads(p.read_text());value['payload']['cwd']='/incorrect-workspace';p.write_text(json.dumps(value)+'\n')
    elif mutation=='task':
        work=c.workspace(root,1);value=c.read(work/'science-task.json');value['goal_text']='Injected objective';c.write(work/'science-task.json',value)
    elif mutation=='schema':
        c.write(first/'science-result.schema.json',dict(type='object',properties={},required=[],additionalProperties=False))
    with pytest.raises(ValueError):controller.worker_resume(root,1)
    assert not (first/'continuations/001/native-intent.json').exists()
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'


def test_missing_meter_is_unknown_not_billing_delay(tmp_path):
    root=instance(tmp_path);first=fail(root)
    (first/'usage.json').unlink()
    originals={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    result=controller.worker_resume(root,1)
    assert result['status']=='worker_resume_unavailable'
    assert result['reason']=='worker_resume_terminal_ineligible'
    assert not (first/'continuations/001/native-intent.json').exists()
    assert native.usage(root)['unknown_steps']==[1]
    assert all(p.read_bytes()==data for p,data in originals.items())
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'


def test_shared_amendment_does_not_change_original_soft_target(tmp_path):
    root=instance(tmp_path,cap=30);first=fail(root)
    before={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    budget.amend(root,max_tokens=5000,max_seconds=500,authorization_note='Explicit shared ceiling only')
    # Original 14 + continuation 22 exceeds the original 30-token soft target.
    assert controller.worker_resume(root,1)['status']=='wait'
    outcome=c.read(first/'continuations/001/native-outcome.json')
    assert outcome['stop_reason'] is None
    assert outcome['budget_slice']['observed_tokens']==36
    assert outcome['budget_slice']['limits']['max_observed_tokens']==30
    assert all(p.read_bytes()==raw for p,raw in before.items())
    assert (first/'result-identity.json').exists()


def test_resume_budget_denial_restores_inactive_clock_and_keeps_attempt_unaccepted(tmp_path,monkeypatch):
    root=instance(tmp_path);first=fail(root)
    outcome=c.read(first/'native-outcome.json')
    assert outcome['returncode']==7 and outcome['stop_reason'] is None
    assert outcome['cleanup']=='observed_group_cleaned'
    originals={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    budget.amend(root,max_tokens=native.usage(root)['accounted_tokens'],authorization_note='Reduced explicit shared budget to actual consumed usage')
    accept=budget.accept_continuation;refused=[]
    def observed_accept(*args,**kwargs):
        try:return accept(*args,**kwargs)
        except budget.NodeBudgetUnavailable as error:
            refused.append(error.reason)
            raise
    with monkeypatch.context() as patch:
        patch.setattr(budget,'accept_continuation',observed_accept)
        result=controller.worker_resume(root,1)
    assert refused==['worker_resume_budget_unavailable']
    # The public controller collects the retained nonzero original after refusal.
    assert result['status']=='native_outcome_unsealed',result
    assert budget.snapshot(root)['clock_phase']=='paused'
    assert not (first/'continuations/001/native-intent.json').exists()
    assert all(p.read_bytes()==raw for p,raw in originals.items())
    budget.amend(root,max_tokens=200,authorization_note='Explicit capacity restoration')
    assert controller.worker_resume(root,1)['status']=='wait'
    assert native.usage(root)['observed_tokens']==36


def test_operator_pause_does_not_accept_a_new_continuation(tmp_path):
    root=instance(tmp_path);first=fail(root)
    c.write(root/'pause.request',dict(reason='user pause'))
    assert controller.worker_resume(root,1)['reason']=='worker_resume_paused_or_closeout'
    assert not (first/'continuations/001/native-intent.json').exists()


def test_unaccepted_intent_is_not_replayed_or_given_another_attempt(tmp_path):
    root=instance(tmp_path);first=fail(root)
    from scidiscovery.research_loop import continuation
    job=c.read(root/'jobs/001.json')
    budget.transition(root,'advance','accepted fixture before runner')
    anchor=budget.accept_continuation(root,job,[first])
    record=first/'continuations/001';record.mkdir(parents=True)
    c.write(record/'native-intent.json',dict(step=1,attempt=1,task_ref=c.record_ref(root/'jobs/001.json'),
        root_intent_ref=c.record_ref(first/'native-intent.json'),previous=execution.proof(first),
        session=continuation.session(root,1,first),budget_slice=anchor))
    with pytest.raises(RuntimeError,match='unknown'):controller.worker_resume(root,1)
    assert not (first/'continuations/002').exists()
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'


def test_real_w_continuation_seals_a_and_reuses_successful_science(tmp_path):
    from test_history_handoff import finish
    from scidiscovery.research_loop import history, search
    root=instance(tmp_path,search_policy=dict(max_w_seconds=10,max_w_observed_tokens=80,max_requested_repairs=2))
    plan=dict(scope='A discriminating mechanism',commitments=[dict(label='M',minimal_test='Bounded screen',existing_ref=None)],focus=dict(label='M',action='screen'))
    finish(root,1,'H',[],action='experiment',search_choice=plan)
    first=fail(root,2)
    saved={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    result=controller.worker_resume(root,2)
    assert result['status']=='finish' and result['completed_steps']==3
    assert all(p.read_bytes()==value for p,value in saved.items())
    node=status(root)['nodes'][1]
    assert node['budget_report']['limits']==dict(max_seconds=10,max_observed_tokens=80)
    assert node['budget_report']['observed_tokens']==36
    view=search.project(root,4)
    assert view['accepted_slices'][0]['meter']['observed_tokens']==36
    assert view['accepted_slices'][0]['meter']['dispatches']==2
    assert c.validate(c.workspace(root,3))['role']=='A'
    sources=history.direct_sources(root,c.read(root/'jobs/003.json'))
    assert next(payload for job,payload,_ in sources if job['role']=='W')['scientific_result']=='Original checkpoint was retained.'
    assert budget.used_steps(root)==4 and native.usage(root)['observed_tokens']==68


def test_continuation_cannot_exceed_consumed_shared_step_budget(tmp_path,monkeypatch):
    from test_history_handoff import finish
    root=instance(tmp_path,steps=2)
    finish(root,1,'H',[],action='experiment')
    first=fail(root,2)
    saved={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    settings=(root/'settings.json').read_bytes()
    job=(root/'jobs/002.json').read_bytes()
    outcome=c.read(first/'native-outcome.json')
    assert outcome['returncode']==7 and outcome['budget_slice']['dispatches']==1
    assert outcome['budget_slice']['observed_tokens']==14
    assert outcome['budget_slice']['limits']==dict(max_observed_tokens=100,max_seconds=20)
    assert budget.protected_final_report(c.settings(root))
    assert budget.delivery_reserve(c.settings(root))==dict(tokens=0,seconds=0.,steps=0)
    accept=budget.accept_continuation;refused=[]
    def observed_accept(*args,**kwargs):
        try:return accept(*args,**kwargs)
        except budget.NodeBudgetUnavailable as error:
            refused.append(error.reason)
            raise
    monkeypatch.setattr(budget,'accept_continuation',observed_accept)
    # H and the failed original W consumed both authorized slots.
    # A continuation requires another slot and cannot be accepted here.
    for attempt in range(2):
        result=controller.worker_resume(root,2)
        assert refused==['worker_resume_budget_unavailable']*(attempt+1)
        assert result['status']=='native_outcome_unsealed',result
        assert controller.run(root)['status']=='native_outcome_unsealed'
        assert len(execution.records(root,2))==1 and not (first/'continuations/001').exists()
        assert not (first/'result-identity.json').exists() and not (root/'jobs/003.json').exists()
        assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'
        assert (c.workspace(root,2)/'work/checkpoint').read_text()=='original checkpoint'
        assert budget.used_steps(root)==2 and budget.effective_max_steps(root)==2
        assert native.usage(root)['observed_tokens']==24 and budget.final_a_barrier(root) is None
        assert budget.snapshot(root)['clock_phase']=='paused'
        assert all(p.read_bytes()==raw for p,raw in saved.items())
        assert (root/'settings.json').read_bytes()==settings and (root/'jobs/002.json').read_bytes()==job




def test_two_explicit_continuations_never_auto_loop_or_refund_attempts(tmp_path):
    root=instance(tmp_path);first=fail(root)
    (c.workspace(root,1)/'work/fail-again').touch()
    for attempt in (1,2):
        with pytest.raises(ValueError):controller.worker_resume(root,1)
        wait_runner(root)
        assert len(execution.records(root,1))-1==attempt
        assert controller.run(root)['status']=='native_outcome_unsealed'
    assert controller.worker_resume(root,1)['reason']=='worker_resume_limit'
    assert native.usage(root)['observed_tokens']==58 and budget.used_steps(root)==3
    assert not (first/'continuations/003').exists()
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\nresume\nresume\n'


@pytest.mark.parametrize('value',[0,-1,True,float('nan')])
def test_stored_previous_token_anchor_cannot_refund_exact_prior_meter(tmp_path,value):
    root=instance(tmp_path);first=fail(root)
    from scidiscovery.research_loop import continuation
    job=c.read(root/'jobs/001.json');budget.transition(root,'advance','accepted negative fixture')
    anchor=budget.accept_continuation(root,job,[first]);anchor['previous_tokens']=value
    record=first/'continuations/001';record.mkdir(parents=True)
    intent=dict(step=1,attempt=1,task_ref=c.record_ref(root/'jobs/001.json'),
        root_intent_ref=c.record_ref(first/'native-intent.json'),previous=execution.proof(first),
        session=continuation.session(root,1,first),budget_slice=anchor)
    # A malicious raw receipt can contain NaN even though the host writer rejects it.
    (record/'native-intent.json').write_text(json.dumps(intent))
    with pytest.raises(ValueError):budget.stop_node(root,job,intent,budget.snapshot(root),dict(input_tokens=0,output_tokens=0))
    assert not (record/'native-process.json').exists()


def test_missing_required_original_report_prevents_qualification(tmp_path):
    root=instance(tmp_path);first=fail(root)
    outcome=c.read(first/'native-outcome.json');del outcome['budget_slice']['input_tokens'];c.write(first/'native-outcome.json',outcome)
    with pytest.raises(ValueError):controller.worker_resume(root,1)
    assert not (first/'continuations/001').exists()


@pytest.mark.parametrize('tamper', ['intent', 'report'])
def test_success_before_seal_does_not_rebind_drifted_attempt(tmp_path,monkeypatch,tamper):
    root=instance(tmp_path);first=fail(root)
    seal=c.seal_result
    def defer(*args):raise ValueError('Fixture leaves successful terminal before seal')
    monkeypatch.setattr(c,'seal_result',defer)
    monkeypatch.setattr(native,'seal_result',defer)
    with pytest.raises(ValueError,match='Fixture leaves'):controller.worker_resume(root,1)
    wait_runner(root)
    monkeypatch.setattr(c,'seal_result',seal)
    monkeypatch.setattr(native,'seal_result',seal)
    current=execution.record(root,1)
    if tamper=='intent':
        value=c.read(current/'native-intent.json');value['budget_slice']['previous_tokens']-=1
        c.write(current/'native-intent.json',value)
    else:
        value=c.read(current/'native-outcome.json');value['budget_slice']['observed_tokens']-=1
        c.write(current/'native-outcome.json',value)
    with pytest.raises(ValueError):c.seal_result(root,1)
    with pytest.raises(ValueError):controller.run(root)
    assert not (first/'result-identity.json').exists()
    assert not (first/'continuations/002').exists()
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\nresume\n'


@pytest.mark.parametrize('tamper',['schema','task'])
def test_accepted_continuation_rechecks_task_and_schema_before_actual_popen(tmp_path,monkeypatch,tamper):
    root=instance(tmp_path);first=fail(root)
    from scidiscovery.research_loop import runtime
    start=runtime.start
    def drift(root,step,attempt=0):
        if tamper=='schema':
            c.write(first/'science-result.schema.json',dict(type='object'))
        else:
            work=c.workspace(root,step);value=c.read(work/'science-task.json');value['goal_text']='Late injection';c.write(work/'science-task.json',value)
        return start(root,step,attempt)
    monkeypatch.setattr(runtime,'start',drift)
    with pytest.raises(ValueError):controller.worker_resume(root,1)
    current=first/'continuations/001'
    assert (current/'native-outcome.json').exists() and not (current/'native-process.json').exists()
    assert not (first/'result-identity.json').exists()
    assert len(execution.records(root,1))==2
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'



def large_native_session_tree(root):
    """Complete session originals, independent of optional live token discovery."""
    directory=root/'native-homes/001/sessions'
    original=directory/'rollout.jsonl'
    value=json.loads(original.read_text());value['payload']['retained_metadata']='original metadata '*5000
    original.write_text(json.dumps(value)+'\n')
    assert original.stat().st_size>65536
    for number in range(257):
        other=dict(type='session_meta',payload=dict(id=f'{number:08d}-0000-4000-8000-000000000000',cwd=str(c.workspace(root,1))))
        (directory/f'other-{number:03d}.jsonl').write_text(json.dumps(other)+'\n')
    assert len(list(directory.glob('*.jsonl')))==258
    return original


def test_large_native_session_tree_and_header_resume_same_original(tmp_path):
    root=instance(tmp_path);first=fail(root);session_file=large_native_session_tree(root)
    reference=continuation.session(root,1,first)
    assert reference['path']==str(session_file)
    with session_file.open('rb') as stream:header=stream.readline()
    assert reference['header']==json.loads(header)
    result=controller.worker_resume(root,1)
    assert result['status']=='wait'
    accepted=c.read(execution.record(root,1)/'native-intent.json')
    assert accepted['session']==reference
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\nresume\n'
    assert native.usage(root)['observed_tokens']==36


def test_large_native_session_header_wrong_cwd_does_not_accept_resume(tmp_path):
    root=instance(tmp_path);first=fail(root);session_file=large_native_session_tree(root)
    value=json.loads(session_file.read_text());value['payload']['cwd']='/incorrect-workspace'
    session_file.write_text(json.dumps(value)+'\n')
    with pytest.raises(ValueError,match='^Native session identity or workspace changed$'):controller.worker_resume(root,1)
    assert not (first/'continuations/001/native-intent.json').exists()
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'


def test_large_native_session_header_hash_drift_cannot_launch_accepted_resume(tmp_path):
    root=instance(tmp_path);first=fail(root);session_file=large_native_session_tree(root)
    reference=continuation.session(root,1,first);job=c.read(root/'jobs/001.json')
    budget.transition(root,'advance','exact original large-header negative control')
    anchor=budget.accept_continuation(root,job,[first]);record=first/'continuations/001';record.mkdir(parents=True)
    c.write(record/'native-intent.json',dict(step=1,attempt=1,task_ref=c.record_ref(root/'jobs/001.json'),
        root_intent_ref=c.record_ref(first/'native-intent.json'),previous=execution.proof(first),
        session=reference,budget_slice=anchor))
    value=json.loads(session_file.read_text());value['payload']['retained_metadata']+='changed after acceptance'
    session_file.write_text(json.dumps(value)+'\n')
    with pytest.raises(ValueError,match='Original native session header changed'):native.run_native(root,1,1)
    assert not (record/'native-process.json').exists()
    assert not (first/'continuations/002').exists()
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'


@pytest.mark.parametrize('shape',['current','legacy16','legacy15','required_drift','other_field'])
def test_resume_accepts_only_exact_published_original_schema_shapes(tmp_path,shape):
    root=instance(tmp_path);job=c.build_job(root,1,'H',[])
    path=execution.origin(root,1)/'science-result.schema.json'
    schema=c.read(path)
    if shape!='current':schema['properties']['implementation_selection']['maxItems']=16
    if shape=='legacy15':schema['properties']['implementation_selection']['maxItems']=15
    elif shape=='required_drift':schema['required'].remove('summary')
    elif shape=='other_field':schema['properties']['scientific_result']['minLength']=2
    c.write(path,schema)
    originals={p:p.read_bytes() for p in (path,root/'jobs/001.json',c.workspace(root,1)/'science-task.json')}
    if shape in {'current','legacy16'}:assert continuation.validate_task(root,1)==job
    else:
        with pytest.raises(ValueError,match='Original native output schema changed'):continuation.validate_task(root,1)
    assert all(p.read_bytes()==data for p,data in originals.items())
    assert not (execution.origin(root,1)/'native-intent.json').exists()


def test_worker_resume_allows_launcher_source_upgrade(tmp_path):
    root=instance(tmp_path);fail(root)
    launcher=Path(c.settings(root)['native_launcher_argv'][1])
    launcher.write_text(launcher.read_text()+'\n# upgraded launcher\n')
    controller.worker_resume(root,1)
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\nresume\n'
