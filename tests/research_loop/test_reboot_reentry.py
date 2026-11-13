"""Reboot accounting and same-session reentry; finite fake native only."""
import json
import sys

import pytest

from scidiscovery.research_loop import accepted_budget, budget, continuation, contracts as c, controller, execution, native, profiles, runtime
from test_worker_resume import FAKE, instance


THREAD = '11111111-2222-4333-8444-555555555555'


def interrupted_instance(tmp_path, monkeypatch, *, formal_review=False, output_only=False, fake=FAKE,
                         closed_clock=False, final=False):
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
    if not final:job = c.build_job(root, 1, 'H', [])
    current = budget.clock_stamp()
    old = dict(at_unix=current['at_unix'] - (current['at_boottime'] + 20. if closed_clock else 10.),
        at_boottime=100., boot_id='previous-host-boot')
    with monkeypatch.context() as clock:
        clock.setattr(budget, 'clock_stamp', lambda: dict(old))
        if final:
            budget.begin_closeout(root, 0, 1, 'Original final report', final_only=True)
            job = c.build_job(root, 1, 'A', [], final_report=dict(source_cutoff=0, stopped_original=None))
        c.write(root/'host-started.json', old)
        budget.transition(root, 'advance', 'original_native_launch')
        anchor = budget.accept_node(root, job)
        record = execution.origin(root, 1)
        accepted_budget.accept_native(root, 1, record, dict(step=1,
            task_ref=c.record_ref(root/'jobs/001.json'), budget_slice=anchor,
            **profiles.binding(c.settings(root,job), job)))
        if closed_clock:
            # A completed control interval on the old boot leaves ten seconds
            # consumed. Its paused reboot gap can span the real current uptime
            # in both this process and the detached runner, without new limits
            # or a synthetic current clock that disappears across exec.
            stopped = dict(old, at_unix=old['at_unix'] + 10., at_boottime=110.)
            clock.setattr(budget, 'clock_stamp', lambda: dict(stopped))
            budget.transition(root, 'paused', 'old_controller_stopped')
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
    if closed_clock:
        saved_paths.append(root/'control-ledger/000002.json')
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
    current = budget.clock_stamp()
    before = budget.snapshot(root, sample_stamp=current)
    assert before['consumed_seconds'] >= 10
    assert before['consumed_seconds'] == current['at_boottime']
    budget.transition(root,'advance','recover_original')
    after = budget.snapshot(root)
    assert after['consumed_seconds'] >= before['consumed_seconds']
    assert after['max_seconds'] == 120
    assert all(path.read_bytes()==data for path,data in saved.items())


def test_final_unknown_original_never_acquires_a_reentry(tmp_path,monkeypatch):
    root,_=interrupted_instance(tmp_path,monkeypatch,closed_clock=True,final=True)
    record=execution.origin(root,1)
    receipt=c.read(record/'runner-process.json')
    c.write(record/'runner-process.json',dict(receipt,task_ref=dict(path='/another/original.json')))
    assert runtime.node_state(root,1)=='unknown'
    grant=budget.final_a_barrier(root)
    before={path:path.read_bytes() for path in (record/'native-intent.json',record/'runner-process.json',record/'usage.json')}
    now=[0.]
    monkeypatch.setattr(native.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(native.time,'sleep',lambda seconds:now.__setitem__(0,now[0]+seconds))
    monkeypatch.setattr(runtime,'start',lambda *args:pytest.fail('Unknown original must not relaunch'))
    result=controller.run(root)
    assert result['status']=='closeout_native_unsealed',result
    assert budget.final_a_barrier(root)==grant
    assert all(path.read_bytes()==data for path,data in before.items())
    assert not (record/'continuations').exists() and not (root/'jobs/002.json').exists()
