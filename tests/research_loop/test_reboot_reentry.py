"""Reboot accounting and same-session reentry; finite fake native only."""
import json
import sys

import pytest

from scidiscovery.research_loop import budget, continuation, contracts as c, controller, execution, native, profiles, runtime
from test_worker_resume import FAKE, instance, wait_runner


THREAD = '11111111-2222-4333-8444-555555555555'


def interrupted_instance(tmp_path, monkeypatch, *, formal_review=False, output_only=False, fake=FAKE):
    if formal_review or output_only:
        public=tmp_path/'public'; public.mkdir()
        objective=public/'objective.md'; objective.write_text('Continue the original bounded task.')
        launcher=tmp_path/'native.py'; launcher.write_text(fake)
        root=tmp_path/'instance'
        controller.prepare(root,objective,public,[sys.executable,str(launcher)],
            max_tokens=1000,max_seconds=120,max_steps=6,model='no-model',reasoning_effort='high',
            search_policy=None,formal_review_policy=dict(version=1) if formal_review else None,
            node_budget_policy={role:dict(max_observed_tokens=100,max_seconds=20) for role in 'HWAE'},
            resume_policy=dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn',
                **(dict(output_only=True,output_repair=True,execution_wake=True,
                    native_home=str(tmp_path/'original-native-home')) if output_only else {})))
    else:
        root = instance(tmp_path, fake=fake)
    job = c.build_job(root, 1, 'H', [])
    current = budget.clock_stamp()
    old = dict(at_unix=current['at_unix']-10, at_boottime=100., boot_id='previous-host-boot')
    with monkeypatch.context() as clock:
        clock.setattr(budget, 'clock_stamp', lambda: dict(old))
        c.write(root/'host-started.json', old)
        budget.transition(root, 'advance', 'original_native_launch')
        anchor = budget.accept_node(root, job)
    record = execution.origin(root, 1)
    c.write(record/'native-intent.json', dict(step=1,
        task_ref=c.record_ref(root/'jobs/001.json'), budget_slice=anchor,
        **profiles.binding(c.settings(root,job), job)))
    binding = execution.identity(root, 1)
    for name in ('runner-process.json','native-process.json','watchdog-process.json'):
        c.write(record/name, dict(pid=999999, boot_id=old['boot_id'], start_ticks=1,
            pgid=999999, sid=999999, ppid=1, task_ref=binding))
    (record/'native.jsonl').write_text(json.dumps(dict(type='thread.started',thread_id=THREAD))+'\n')
    c.write(record/'usage.json',dict(input_tokens=10,cached_input_tokens=3,output_tokens=4))
    home = execution.home(root,1); (home/'sessions').mkdir(parents=True)
    session = home/'sessions/original.jsonl'
    session.write_text(json.dumps(dict(type='session_meta',payload=dict(id=THREAD,cwd=str(c.workspace(root,1)))))+'\n')
    work = c.workspace(root,1)
    (work/'work').mkdir(exist_ok=True)
    (work/'artifacts').mkdir(exist_ok=True)
    (work/'work/checkpoint').write_text('original checkpoint')
    (work/'artifacts/retained.txt').write_text('original scientific artifact')
    c.write(work/'work/original-call.json', dict(call_id='unchanged-original-call',status='unknown'))
    saved_paths = [root/'settings.json', root/'jobs/001.json', root/'host-started.json',
        root/'control-ledger/000001.json', session, work/'work/checkpoint',
        work/'artifacts/retained.txt', work/'work/original-call.json',
        *[record/name for name in ('native-intent.json','native.jsonl','usage.json',
            'runner-process.json','native-process.json','watchdog-process.json')]]
    return root, {path:path.read_bytes() for path in saved_paths}


def test_cross_boot_timestamp_gap_keeps_recorded_consumption():
    first = dict(at_unix=100.,at_boottime=10.,boot_id='a')
    later = dict(at_unix=104.,at_boottime=16.,boot_id='a')
    restarted = dict(at_unix=124.,at_boottime=2.,boot_id='b')
    assert budget._duration(first,later) == 6  # Retain existing measured cost.
    assert budget._duration(later,restarted) == 20
    assert budget._duration(later,dict(restarted,at_unix=90.)) == 0


def test_cross_boot_snapshot_charges_open_interval_without_reset(tmp_path,monkeypatch):
    root, saved = interrupted_instance(tmp_path,monkeypatch)
    before = budget.snapshot(root)
    assert before['consumed_seconds'] >= 10
    budget.transition(root,'advance','recover_original')
    after = budget.snapshot(root)
    assert after['consumed_seconds'] >= before['consumed_seconds']
    assert after['max_seconds'] == 120
    assert all(path.read_bytes()==data for path,data in saved.items())


def test_reentry_retains_same_job_session_budget_and_is_idempotent(tmp_path,monkeypatch):
    root, saved = interrupted_instance(tmp_path,monkeypatch)
    starts = []

    def start(selected,step,attempt=0):
        starts.append((selected,step,attempt))
        record=execution.record(selected,step,attempt)
        binding=execution.identity(selected,step,attempt)
        for name in ('runner-process.json','native-process.json'):
            c.write(record/name,dict(pid=123,boot_id=budget.clock_stamp()['boot_id'],
                start_ticks=1,pgid=123,sid=123,ppid=1,task_ref=binding))

    monkeypatch.setattr(runtime,'start',start)
    monkeypatch.setattr(runtime,'process_matches',lambda identity,binding:
        identity.get('pid')==123 and identity.get('task_ref')==binding)
    assert runtime.node_state(root,1)=='interrupted'
    intent=continuation.reenter(root,1)
    assert continuation.reenter(root,1)==intent
    assert starts==[(root,1,1)]
    assert runtime.node_state(root,1)=='running'
    assert intent['session']['thread_id']==THREAD
    assert intent['session']['workspace']==str(c.workspace(root,1))
    assert intent['session']['home']==str(root/'native-homes/001')
    assert intent['budget_slice']['previous_tokens']==14
    assert intent['budget_slice']['previous_seconds']>=10
    assert budget.used_steps(root)==1
    assert native.usage(root)['observed_tokens']==14
    assert not (execution.origin(root,1)/'native-outcome.json').exists()
    assert all(path.read_bytes()==data for path,data in saved.items())


def test_live_native_process_prevents_reentry_when_runner_is_stale(tmp_path,monkeypatch):
    root,_=interrupted_instance(tmp_path,monkeypatch)
    record=execution.record(root,1)
    child=c.read(record/'native-process.json');child['pid']=123
    c.write(record/'native-process.json',child)
    monkeypatch.setattr(runtime,'process_matches',lambda identity,binding:identity.get('pid')==123)
    monkeypatch.setattr(runtime,'start',lambda *args:pytest.fail('Duplicated a live original native process'))
    assert runtime.node_state(root,1)=='running'
    continuation.reenter(root,1)
    assert execution.records(root,1)==[record]


def test_second_reboot_after_checkpoint_keeps_cost_and_uses_current_launch_boot(tmp_path,monkeypatch):
    root,_=interrupted_instance(tmp_path,monkeypatch)
    with monkeypatch.context() as crash:
        crash.setattr(execution,'proof',lambda *args:(_ for _ in ()).throw(RuntimeError('interrupted before intent')))
        with pytest.raises(RuntimeError,match='interrupted before intent'):
            continuation.reenter(root,1)
    marker=execution.origin(root,1)/'interruption.json'
    saved=marker.read_bytes()
    now=budget.clock_stamp()
    monkeypatch.setattr(budget,'clock_stamp',lambda:dict(at_unix=now['at_unix']+20,
        at_boottime=2.,boot_id='another-host-boot'))
    starts=[]
    monkeypatch.setattr(runtime,'start',lambda *args:starts.append(args))
    intent=continuation.reenter(root,1)
    assert intent['budget_slice']['boot_id']=='another-host-boot'
    assert budget._node_duration(root,c.read(root/'jobs/001.json'),intent,budget.snapshot(root))>=30
    assert marker.read_bytes()==saved
    assert len(starts)==1 and budget.used_steps(root)==1


def test_output_only_repair_policy_reenters_original_full_task_and_inherited_home(tmp_path,monkeypatch):
    root,saved=interrupted_instance(tmp_path,monkeypatch,output_only=True)
    starts=[]
    monkeypatch.setattr(runtime,'start',lambda *args:starts.append(args))
    original=c.read(execution.origin(root,1)/'native-intent.json')
    intent=continuation.reenter(root,1)
    assert intent['session']['home']==str(tmp_path/'original-native-home')
    assert execution.home(root,1,0)==execution.home(root,1,1)==tmp_path/'original-native-home'
    assert intent.get('output_repair') is None and intent.get('execution_wake') is None
    assert intent.get('native_profile')==original.get('native_profile')
    assert intent['budget_slice']['limits']==original['budget_slice']['limits']
    assert intent['budget_slice']['previous_tokens']==14
    assert len(starts)==1 and budget.used_steps(root)==1
    assert all(path.read_bytes()==data for path,data in saved.items())


def test_output_only_policy_still_refuses_new_work_resume_after_completed_failure(tmp_path,monkeypatch):
    root,_=interrupted_instance(tmp_path,monkeypatch,output_only=True)
    c.write(execution.origin(root,1)/'native-outcome.json',dict(returncode=7,stop_reason=None))
    monkeypatch.setattr(runtime,'start',lambda *args:pytest.fail('Output-only policy granted new work'))
    with pytest.raises(continuation.ResumeUnavailable,match='worker_resume_output_only'):
        continuation.start(root,1)
    assert len(execution.records(root,1))==1


def test_output_only_execution_wake_cannot_reenter_as_ordinary_full_task(tmp_path,monkeypatch):
    root,_=interrupted_instance(tmp_path,monkeypatch,output_only=True)
    record=execution.origin(root,1)
    original=c.read(record/'native-intent.json')
    original.update(continuation_kind='execution_wake',execution_wake=dict(waiting='original-only-wake'))
    c.write(record/'native-intent.json',original)
    monkeypatch.setattr(runtime,'start',lambda *args:pytest.fail('Output-only wake gained ordinary task permissions'))
    with pytest.raises(continuation.ResumeUnavailable,match='worker_resume_interrupted_scope_unavailable'):
        continuation.reenter(root,1)
    assert len(execution.records(root,1))==1
    assert not (record/'interruption.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('output_only',[False,True],ids=['ordinary','inherited_output_only'])
@pytest.mark.parametrize('crash_before_spawn',[False,True])
def test_run_and_continue_collect_same_session_without_replaying_original(tmp_path,monkeypatch,crash_before_spawn,output_only):
    from uuid import NAMESPACE_URL, uuid5
    from scidiscovery.research_loop import instance_control
    fake=FAKE.replace('import json,os,pathlib,sys', 'import json,os,pathlib,sys,uuid', 1).replace(
        f'thread={THREAD!r}',
        f"thread={THREAD!r} if 'resume' in args else str(uuid.uuid5(uuid.NAMESPACE_URL,str(work)))\n"
        "if 'resume' not in args:\n"
        " p=home/'sessions'/('rollout-'+work.name+'-'+thread+'.jsonl');p.parent.mkdir(parents=True,exist_ok=True)\n"
        " p.write_text(json.dumps({'type':'session_meta','payload':{'id':thread,'cwd':str(work)}})+'\\n')", 1)
    root,saved=interrupted_instance(tmp_path,monkeypatch,formal_review=True,output_only=output_only,fake=fake)
    assert c.read(root/'jobs/001.json')['formal_review_version']==1
    # The supported prepare entry freezes this capability; do not amend its bytes.
    assert c.settings(root)['final_a_completion_policy']==dict(version=1)
    if crash_before_spawn:
        with monkeypatch.context() as crash:
            crash.setattr(runtime,'start',lambda *args:(_ for _ in ()).throw(RuntimeError('interrupted before spawn')))
            with pytest.raises(RuntimeError,match='interrupted before spawn'):
                continuation.reenter(root,1)
        # Simulate a further boot while this same accepted reentry has not run.
        pending=execution.record(root,1)/'native-intent.json'
        accepted=c.read(pending);accepted['budget_slice']['boot_id']='previous-reentry-boot'
        c.write(pending,accepted)
    result=controller.run(root)
    h_outcome=c.read(execution.record(root,1)/'native-outcome.json')
    diagnostic=dict(status=result.get('status'),reason=result.get('reason'),
        step=result.get('attempt_step',result.get('last_step')),
        has_job_002=(root/'jobs/002.json').exists(),execution_blocked=budget.execution_blocked(root),
        h_outcome={key:h_outcome.get(key) for key in ('returncode','stop_reason','cleanup')})
    assert result['status']=='final_a_completed',diagnostic
    assert result['reason']=='accepted_worker_evidence_unavailable',result
    wait_runner(root)
    wait_runner(root,2)
    first,current=execution.records(root,1)
    expected_home=tmp_path/'original-native-home' if output_only else root/'native-homes/001'
    assert execution.home(root,1,0)==execution.home(root,1,1)==expected_home
    assert c.read(current/'native-intent.json')['session']['home']==str(expected_home)
    assert (c.workspace(root,1)/'work/launches').read_text()=='resume\n'
    assert not (first/'native-outcome.json').exists()
    assert c.read(current/'native-outcome.json')['thread_id']==THREAD
    seal=c.read(first/'result-identity.json')
    assert seal.get('native_authorship') is not None
    from scidiscovery.research_loop import formal_review
    participants=formal_review.participants(root,c.read(root/'jobs/001.json'),seal)
    assert participants['complete'] is False
    authorship=formal_review.q._read(root,seal['native_authorship'])
    assert authorship['attempts'][0]['outcome'] is None
    assert authorship['attempts'][0]['status']=='unsupported'
    assert sum(c.read(path/'usage.json')['input_tokens']+c.read(path/'usage.json')['output_tokens']
        for path in (first,current))==36
    assert c.read(current/'native-outcome.json')['budget_slice']['observed_tokens']==36
    final_job=c.read(root/'jobs/002.json')
    assert final_job['role']=='A' and final_job['final_only'] is True
    final_record,=execution.records(root,2)
    assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'
    final_thread=str(uuid5(NAMESPACE_URL,str(c.workspace(root,2))))
    assert final_thread!=THREAD
    assert c.read(final_record/'native-outcome.json')['thread_id']==final_thread
    final_home=execution.home(root,2)
    assert final_home==(expected_home if output_only else root/'native-homes/002')
    final_session=final_home/'sessions'/('rollout-002-'+final_thread+'.jsonl')
    assert json.loads(final_session.read_text())['payload']==dict(id=final_thread,cwd=str(c.workspace(root,2)))
    assert budget.final_a_barrier(root)['step']==2
    begin=budget.closeout_state(root)['begin']
    assert begin['mode']=='final' and begin['source_cutoff']==1 and begin['first_step']==2
    assert begin['stop_reason']=='scientific_wait' and begin.get('resume_cursor') is None
    assert native.usage(root)['observed_tokens']==58
    assert native.usage(root)['unknown_steps']==[1]  # Interrupted billing remains a lower bound.
    assert budget.used_steps(root)==2
    usage=native.usage(root)
    assert controller.run(root)['status']=='final_a_completed'
    assert instance_control.continue_instance(root)['status']=='final_a_completed'
    assert not (root/'jobs/003.json').exists() and native.usage(root)==usage
    assert (c.workspace(root,1)/'work/launches').read_text()=='resume\n'
    assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'
    from scidiscovery.research_loop import qualification as q
    read=c.read
    for wrong_raw in (first/'science-output.json',final_record/'science-output.json'):
        def different_original(path):
            value=read(path)
            if str(path)==str(first/'result-identity.json'):
                return dict(value,raw_ref=dict(value['raw_ref'],path=str(wrong_raw)))
            return value
        with monkeypatch.context() as changed:
            changed.setattr(c,'read',different_original)
            with pytest.raises(q.IntegrityError,match='Direct source does not name its exact producer original'):
                q._inputs(root,final_job)
    assert all(path.read_bytes()==data for path,data in saved.items())
