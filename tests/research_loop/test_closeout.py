"""Closeout through real local fake science and isolated provider processes."""
import json
import os
import signal
import time
import subprocess
from pathlib import Path
import shutil
import sys

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, delivery, native, runtime
from test_delivery import FAKE_HARNESS, descriptor, delivery_policy, prepare_fixture, closeout_decision, accepted


FAKE_NATIVE = '''import json,os,pathlib,sys
args=sys.argv[1:];sys.stdin.read();work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);role=os.environ['THIN_HWA_ROLE']
root=pathlib.Path(os.environ['THIN_HWA_JOB_MANIFEST']).parents[1]
payload=dict(summary='Finite '+role+' contribution',scientific_result='Original fake scientific observation',limitations=['Fake transport; no scientific qualification'],implementation_selection=None)
if role=='H':payload.update(next_action='experiment',route_reason='One original experiment',search_plan=None)
if role=='W':
 payload.update(stage_intent='review',mechanism_findings=None)
 source=work/'artifacts/train.py';source.write_text('def train(train_df,val_df): return None\\n')
 model=root/'runs/exact-model.bin';model.write_bytes(b'Exact snapshot, never host-unpickled')
 (root/'runs/results.jsonl').write_text(json.dumps(dict(run_id=1,ok=True,val_acc=.6,val_score=.6,model_path='/opt/exp/runs/exact-model.bin',script=str(source)))+'\\n')
if role=='A':
 task=json.loads((work/'science-task.json').read_text());assert task['closeout']['source_cutoff']==2
 payload.update(next_action='deliver',route_reason='A decides delivery within declared limits',candidate_recommendation='run-1',progress_assessment=None,search_review=None,decision_request=None,portfolio_review=None)
if isinstance(payload.get('candidate_recommendation'),str):payload['selection_explanation']=dict(criteria='Compare the declared public fixture evidence and retain its stated limitations.',criteria_change=dict(status='unchanged',prior_criteria=None,changes=None,evidence=None,accepted_tradeoffs=None))
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))),flush=True)
'''


def real_chain(tmp_path, *, max_steps=5, launcher_text=FAKE_NATIVE, pause_after_worker=False):
    if shutil.which('bwrap') is None:
        pytest.skip('Real provider namespace required')
    public = tmp_path / 'public';(public / 'harness').mkdir(parents=True)
    root = tmp_path / 'instance'
    for name in ('exp_cli.py', 'runner.py', 'train.csv', 'val.csv', 'meta.json'):
        path = public / 'harness' / name
        if name == 'exp_cli.py':
            path.write_text(FAKE_HARNESS)
        elif name == 'meta.json':
            c.write(path, dict(mode='ok', control_path=str(root / 'settings.json'), hidden_path=str(tmp_path / 'hidden.json')))
        else:
            path.write_text('Fixture public data only\n')
    c.write(tmp_path / 'hidden.json', dict(secret='Must never be visible to provider'))
    objective = public / 'objective.md';objective.write_text('Choose an exact candidate and deliver; scientific completion remains unasserted.')
    if pause_after_worker:launcher_text+="\nif role=='W':(root/'pause.request').touch()\n"
    launcher = tmp_path / 'native.py';launcher.write_text(launcher_text)
    nodes = {role:dict(max_observed_tokens=2000,max_seconds=60) for role in ('H','W','A','E')}
    controller.prepare(root,objective,public,[sys.executable,str(launcher)],max_tokens=100_000,max_seconds=300,max_steps=max_steps,
        model='no-model',reasoning_effort='high',search_policy=None,node_budget_policy=nodes,
        history_observer=dict(adapter=str(Path(__file__).resolve().parents[2]/'scripts/observe_covshift_candidates.py'),config=dict(runs='runs',evaluation_key='fixture-public')),
        delivery_policy=delivery_policy(descriptor(root,public)))
    (root/'runs').mkdir();(root/'submission').mkdir()
    return root


@pytest.fixture
def historical_delivery_reserve(monkeypatch):
    """Freeze the pre-FINAL soft reserve before any original job or ledger fact."""
    prepare = controller.prepare
    def prepare_historical(root, *args, **kwargs):
        prepare(root, *args, **kwargs)
        assert not list((root/'control-ledger').glob('*.json'))
        assert not list((root/'jobs').glob('*.json'))
        settings = c.settings(root)
        settings.pop('final_a_completion_policy')
        c.write(root/'settings.json', settings)
        c.write(root/'settings-identity.json', dict(sha256=c.digest(root/'settings.json')))
        assert settings['node_budget_mode']=='soft'
        assert budget.delivery_reserve(settings)==dict(tokens=1100, seconds=65., steps=3)
        assert budget.closeout_floor(settings)==dict(tokens=100, seconds=35., steps=2)
        return settings
    monkeypatch.setattr(controller, 'prepare', prepare_historical)


def native_diagnostics(root):
    names={'native.stderr','runner.stderr','native-outcome.json'}
    paths=sorted(path for path in (root/'native-evidence').rglob('*') if path.is_file() and path.name in names)
    details={}
    for path in paths[:12]:
        if path.name=='native-outcome.json':
            outcome=c.read(path)
            value={key:outcome[key] for key in ('returncode','stop_reason','native_started','cleanup_confirmed','error','note') if key in outcome}
        else:
            value=path.read_bytes()[-4096:].decode(errors='replace')
        if value:details[path.relative_to(root).as_posix()]=value
    print(json.dumps(details,ensure_ascii=False,indent=2))
    return 'Bounded native diagnostics are in captured stdout'


def test_actual_fake_h_w_closeout_a_submit_confirm_and_no_replay(tmp_path):
    root = real_chain(tmp_path,pause_after_worker=True)
    paused=controller.run(root)
    assert paused['status']=='paused' and paused['completed_steps']==2,(paused,native_diagnostics(root))
    assert all((c.host_record(c.workspace(root,step))/'result-identity.json').is_file() for step in (1,2))
    assert not (root/'jobs/003.json').exists()
    (root/'pause.request').unlink()
    result = controller.closeout(root)
    assert result['status']=='delivery_confirmed',(result,native_diagnostics(root))
    assert result['last_step']==3 and result['source_cutoff']==2
    assert result['completed_scientific_nodes']==3 and result['used_steps']==5
    assert result['goal_completion']=='not_asserted' and 'unmet_goal' not in result
    assert native.usage(root)['observed_tokens']==30
    jobs=[c.read(root/'jobs'/f'{step:03d}.json') for step in (1,2,3)]
    assert [job['role'] for job in jobs]==['H','W','A']
    assert jobs[2]['purpose']=='closeout' and [row['step'] for row in jobs[2]['history']['nodes']]==[1,2]
    assert jobs[2]['delivery_sources']['items'][0]['status']=='available'
    facts=budget.closeout_state(root)
    assert [row['intent']['action_kind'] for row in facts['actions']]==['submit','confirm']
    assert not facts['unresolved_operations'] and not budget.execution_blocked(root)
    original={str(p):p.read_bytes() for p in (root/'native-evidence').rglob('*') if p.is_file()}
    result=controller.run(root)
    assert result['status']=='delivery_confirmed',(result,native_diagnostics(root))
    assert all(Path(path).read_bytes()==data for path,data in original.items())
    assert len(budget.closeout_state(root)['actions'])==2


def test_export_only_existing_model_closeout_never_executes(tmp_path,monkeypatch):
    root=real_chain(tmp_path,pause_after_worker=True)
    paused=controller.run(root)
    assert paused['status']=='paused' and paused['completed_steps']==2,(paused,native_diagnostics(root))
    assert all((c.host_record(c.workspace(root,step))/'result-identity.json').is_file() for step in (1,2))
    assert not (root/'jobs/003.json').exists()
    (root/'pause.request').unlink()
    result=controller.closeout(root)
    assert result['status']=='delivery_confirmed',(result,native_diagnostics(root))
    monkeypatch.setattr(native,'execute_native',lambda *a:pytest.fail('Export must never launch science'))
    monkeypatch.setattr(delivery,'invoke',lambda *a,**k:pytest.fail('Export must never invoke an external action'))
    before=len(budget.closeout_state(root)['actions'])
    assert controller.closeout(root,export_only=True)['status']=='exported_unsubmitted'
    assert len(budget.closeout_state(root)['actions'])==before


def test_audit_only_launch_window_and_malformed_phase_are_unknown(tmp_path):
    root, _, nodes = prepare_fixture(tmp_path)
    refs = delivery.freeze_sources(root, nodes)
    _, _, binding = closeout_decision(root, refs)
    submit = accepted(root, binding)
    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(.1)'], start_new_session=True)
    identity = runtime.process_identity(child.pid, binding['operation_id'])
    budget.action_process(root, submit, 'audit', identity)
    child.wait()
    assert budget.execution_blocked(root, ignore_operation=binding['operation_id'])
    with pytest.raises(RuntimeError, match='unresolved execution'):
        accepted(root, binding, 'confirm')
    # A final-phase identity is required, and malformed metadata is never death proof.
    budget.action_process(root, submit, 'promote', identity)
    path = root/'host-delivery/action-processes'/ (submit['action_id']+'-promote.json')
    valid = path.read_bytes()
    fact = c.read(path);fact['identity']['task_sha256']='wrong'
    c.write(path, fact)
    assert budget.execution_blocked(root, ignore_operation=binding['operation_id'])
    path.write_bytes(valid)
    assert not budget.execution_blocked(root, ignore_operation=binding['operation_id'])
    confirm = accepted(root, binding, 'confirm')
    budget.action_outcome(root, confirm, dict(status='confirmed', operation_id=binding['operation_id'],
        candidate_id=binding['candidate_id'], artifact_sha256=binding['source']['objects']['artifact']['sha256'],
        note='Same exact target verified', cleanup_confirmed=True))
    assert not budget.execution_blocked(root)
    budget.inactive(root, 'query_finished')
    assert budget.snapshot(root)['clock_phase']=='paused'
    assert budget.amend(root, max_tokens=110_000, authorization_note='External test operator')['consumption']=='retained'
    assert budget.closeout_state(root)['actions'][0]['outcome'] is None


def test_exhausted_total_tokens_blocks_zero_model_delivery(tmp_path, monkeypatch):
    root, _, nodes = prepare_fixture(tmp_path)
    refs = delivery.freeze_sources(root, nodes)
    _, _, binding = closeout_decision(root, refs)
    before = {p.name:p.read_bytes() for p in (root/'control-ledger').glob('*.json')}
    monkeypatch.setattr(native, 'usage', lambda _:dict(observed_tokens=100_000, unknown_steps=[]))
    with pytest.raises(budget.NodeBudgetUnavailable, match='closeout_budget_unavailable'):
        accepted(root, binding)
    assert {p.name:p.read_bytes() for p in (root/'control-ledger').glob('*.json')}==before


def test_historical_actual_popen_protects_reserve_with_original_anchor(tmp_path, monkeypatch, historical_delivery_reserve):
    root = real_chain(tmp_path)
    now = [100.]
    monkeypatch.setattr(budget.time, 'time', lambda:now[0])
    monkeypatch.setattr(budget.time, 'clock_gettime', lambda _:now[0])
    c.write(root/'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', 'Original accepted science')
    job = c.build_job(root,1,'H',[])
    record = c.host_record(c.workspace(root,1))
    c.write(record/'native-intent.json',dict(step=1,task_sha256=c.digest(root/'jobs/001.json'),budget_slice=budget.accept_node(root,job)))
    original=(record/'native-intent.json').read_bytes()
    prepared=(root/'jobs/001.json').read_bytes()
    now[0]=340.  # 60 seconds remain: below the minimal soft closeout reserve=65.
    monkeypatch.setattr(native.subprocess,'Popen',lambda *a,**k:pytest.fail('Protected reserve must stop actual submission'))
    native.run_native(root,1)
    outcome=c.read(record/'native-outcome.json')
    assert outcome['native_started'] is False and outcome['stop_reason']=='closeout_reserve_seconds'
    assert (record/'native-intent.json').read_bytes()==original
    assert (root/'jobs/001.json').read_bytes()==prepared
    assert budget.snapshot(root)['advance_seconds']==240.
    assert not (record/'native-process.json').exists()


def test_historical_build_time_reserve_refusal_keeps_prepared_job_without_intent(tmp_path, monkeypatch, historical_delivery_reserve):
    root = real_chain(tmp_path)
    now=[100.]
    monkeypatch.setattr(budget.time,'time',lambda:now[0])
    monkeypatch.setattr(budget.time,'clock_gettime',lambda _:now[0])
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'advance','Admitted preparation')
    job=c.build_job(root,1,'H',[])
    now[0]=340.  # Staging used the remaining soft exploration headroom; reserve=65.
    before=(root/'jobs/001.json').read_bytes()
    with pytest.raises(budget.NodeBudgetUnavailable,match='closeout_reserve_seconds'):
        native.execute_native(root,1)
    assert (root/'jobs/001.json').read_bytes()==before
    assert not (c.host_record(c.workspace(root,1))/'native-intent.json').exists()
    assert budget.snapshot(root)['advance_seconds']==240.
    assert controller.run(root)['status']=='retained_job_conflict'
    assert budget.closeout_state(root)['begin'] is None
    assert controller.closeout(root,export_only=True)['status']=='exported_unsubmitted'
    assert budget.closeout_state(root)['begin']['first_step']==2
    assert (root/'jobs/001.json').read_bytes()==before


def test_export_only_does_not_seal_retained_success_or_write_status_cache(tmp_path, monkeypatch):
    root=real_chain(tmp_path)
    job=c.build_job(root,1,'H',[])
    record=c.host_record(c.workspace(root,1))
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'advance','Retained original')
    c.write(record/'native-intent.json',dict(step=1,task_sha256=c.digest(root/'jobs/001.json'),budget_slice=budget.accept_node(root,job)))
    native.run_native(root,1)  # Native success alone is not a science seal.
    original={str(p):p.read_bytes() for p in record.rglob('*') if p.is_file()}
    monkeypatch.setattr(c,'seal_result',lambda *a:pytest.fail('Pure export never seals'))
    monkeypatch.setattr(native,'execute_native',lambda *a:pytest.fail('Pure export never launches'))
    result=controller.closeout(root,export_only=True)
    assert result['status']=='exported_unsubmitted' and result['source_cutoff']==0
    assert not (record/'result-identity.json').exists()
    assert all(Path(path).read_bytes()==data for path,data in original.items())
    from scidiscovery.research_loop.cli import status
    before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert status(root)['closeout']['source_cutoff']==0
    assert before=={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_explicit_export_without_adapter_keeps_empty_prefix_without_science(tmp_path, monkeypatch):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Original objective')
    root=tmp_path/'instance'
    policy=delivery_policy(None);policy.update(submit_seconds=0,confirm_seconds=0)
    controller.prepare(root,objective,public,['fake-no-model'],max_tokens=100_000,max_seconds=300,max_steps=5,
        model='fixture-no-model',reasoning_effort='high',
        node_budget_policy={role:dict(max_observed_tokens=2000,max_seconds=60) for role in ('H','W','A','E')},
        delivery_policy=policy,search_policy=None)
    monkeypatch.setattr(native,'execute_native',lambda *a:pytest.fail('No adapter only exports'))
    monkeypatch.setattr(delivery,'freeze_sources',lambda *a:pytest.fail('No adapter has no provider resolver'))
    monkeypatch.setattr(delivery,'catalogue_sources',lambda *a,**k:pytest.fail('Pure export never resolves candidates'))
    monkeypatch.setattr(delivery,'invoke',lambda *a,**k:pytest.fail('Pure export never invokes external delivery'))
    assert c.settings(root)['final_a_completion_policy']==dict(version=1)
    result=controller.closeout(root,export_only=True)
    assert result['status']=='exported_unsubmitted' and result['source_cutoff']==0 and result['last_step'] is None
    assert not budget.closeout_state(root)['actions'] and not list((root/'jobs').glob('*.json'))
    assert budget.final_a_barrier(root) is None


@pytest.mark.parametrize('boundary', ['accept', 'popen'])
def test_historical_closeout_a_keeps_execution_floor_after_preparation(tmp_path, monkeypatch, boundary, historical_delivery_reserve):
    now=[100.]
    monkeypatch.setattr(budget.time,'time',lambda:now[0])
    monkeypatch.setattr(budget.time,'clock_gettime',lambda _:now[0])
    root, _, nodes=prepare_fixture(tmp_path)
    refs=delivery.freeze_sources(root,nodes)
    budget.begin_closeout(root,2,3,'Original reserve boundary',delivery_sources=refs,preparation_seconds=2.)
    job=c.build_job(root,3,'A',[1,2],purpose='closeout',source_cutoff=2,delivery_sources=refs)
    record=c.host_record(c.workspace(root,3))
    before=(root/'jobs/003.json').read_bytes()
    if boundary=='accept':
        now[0]=368.  # 32s remains: below the actual cleanup(3)+submit(20)+confirm(10) floor.
        with pytest.raises(budget.NodeBudgetUnavailable,match='closeout_budget_unavailable'):
            native.execute_native(root,3)
        assert not (record/'native-intent.json').exists()
    else:
        anchor=budget.accept_node(root,job)
        c.write(record/'native-intent.json',dict(step=3,task_sha256=c.digest(root/'jobs/003.json'),budget_slice=anchor))
        # Spend beyond the original A allocation, but less than its reserved
        # execution floor. Popen checks that floor using the original anchor.
        now[0]=368.  # Remaining 32s < cleanup(3)+submit(20)+confirm(10).
        original=(record/'native-intent.json').read_bytes()
        monkeypatch.setattr(native.subprocess,'Popen',lambda *a,**k:pytest.fail('No closeout science may consume its action floor'))
        native.run_native(root,3)
        outcome=c.read(record/'native-outcome.json')
        assert outcome['native_started'] is False and outcome['stop_reason']=='closeout_budget_unavailable'
        assert (record/'native-intent.json').read_bytes()==original
    assert (root/'jobs/003.json').read_bytes()==before
    assert not (record/'native-process.json').exists()
    assert not (record/'result-identity.json').exists()


def test_resolver_cleanup_unknown_keeps_advance_and_blocks_amendment(tmp_path, monkeypatch):
    root=real_chain(tmp_path)
    now=[100.]
    monkeypatch.setattr(budget.time,'time',lambda:now[0])
    monkeypatch.setattr(budget.time,'clock_gettime',lambda _:now[0])
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'advance','Bounded resolver')
    begin=budget.begin_closeout(root,0,1,'delivery_cleanup_unknown',export_only=True)
    assert budget.execution_blocked(root) and not budget.closeout_state(root)['actions']
    now[0]=110.
    assert controller.closeout(root,export_only=True)['status']=='exported_unsubmitted'
    assert budget.snapshot(root)['clock_phase']=='advance'
    assert budget.snapshot(root)['advance_seconds']==10.
    before={p.name:p.read_bytes() for p in (root/'control-ledger').glob('*.json')}
    with pytest.raises(RuntimeError,match='unknown original execution'):
        budget.amend(root,max_seconds=400,authorization_note='External operator; unknown work remains')
    assert before=={p.name:p.read_bytes() for p in (root/'control-ledger').glob('*.json')}
    assert budget.closeout_state(root)['begin']==begin


def test_dead_promote_leader_with_live_child_blocks_confirm_admission(tmp_path):
    root, _, nodes=prepare_fixture(tmp_path)
    refs=delivery.freeze_sources(root,nodes)
    _, _, binding=closeout_decision(root,refs)
    submit=accepted(root,binding)
    release=tmp_path/'release-leader'
    code=('import pathlib,subprocess,sys,time; '
          'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(20)"]); '
          'print(p.pid,flush=True); '
          'release=pathlib.Path(sys.argv[1]); '
          'exec("while not release.exists(): time.sleep(.01)")')
    leader=subprocess.Popen([sys.executable,'-c',code,str(release)],start_new_session=True,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    child_id=None
    try:
        child_pid=int(leader.stdout.readline())
        child_id=runtime.process_identity(child_pid,binding['operation_id'])
        identity=runtime.process_identity(leader.pid,binding['operation_id'])
        budget.action_process(root,submit,'promote',identity)
        release.touch();leader.wait(timeout=3)
        assert not runtime.process_matches(identity,binding['operation_id'])
        assert runtime.process_matches(child_id,binding['operation_id'])
        assert budget.execution_blocked(root,ignore_operation=binding['operation_id'])
        before=len(budget.closeout_state(root)['actions'])
        with pytest.raises(RuntimeError,match='unresolved execution'):
            accepted(root,binding,'confirm')
        assert len(budget.closeout_state(root)['actions'])==before
    finally:
        if child_id is not None and runtime.process_matches(child_id,binding['operation_id']):
            os.kill(child_id['pid'],signal.SIGKILL)  # Exact owned fixture PID, never a stale group.
        release.touch()
        if leader.poll() is None:leader.kill();leader.wait()
        leader.stdout.close();leader.stderr.close()
        if child_id is not None:
            deadline=time.monotonic()+3
            while runtime.process_matches(child_id,binding['operation_id']) and time.monotonic()<deadline:
                time.sleep(.01)
            try:os.waitpid(child_id['pid'],os.WNOHANG)
            except ChildProcessError:pass
