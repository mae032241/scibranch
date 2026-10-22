"""Positive shared budget admits soft targets; actual exhaustion blocks effects."""
import sys

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, native
from test_history_handoff import finish


def fixture(tmp_path, *, mode='soft', seconds=60, tokens=6_000_000, steps=24, spent=0):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Continue the scientific comparison while preserving necessary delivery resources.')
    launcher=tmp_path/'native.py'
    launcher.write_text("import json,os,pathlib,sys\na=sys.argv[1:];sys.stdin.read()\nrole=os.environ['THIN_HWA_ROLE']\nassert role in ('W','A')\np=dict(summary='W completed',scientific_result='Accepted scientific work retained',limitations=['Fake transport; no managed scientific observation'],implementation_selection=None)\nif role=='W':\n job=json.loads(pathlib.Path(os.environ['THIN_HWA_JOB_MANIFEST']).read_text())\n assigned=(job.get('search') or {}).get('coverage_obligation',[])\n labels={row['mechanism_ref']:row['label'] for row in ((job.get('search') or {}).get('breadth') or {}).get('mechanisms',[])}\n p.update(stage_intent='review',mechanism_findings=[dict(mechanism=labels[row['mechanism_ref']],basis='blocked',finding='This transport fixture performs no managed scientific observation.') for row in assigned] if assigned else None)\nif role=='A':p.update(search_review=None,candidate_recommendation=None,progress_assessment=None,decision_request=None,portfolio_review=None,next_action='wait',route_reason='A retained its conclusion')\nprint(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))),flush=True)\npathlib.Path(a[a.index('--output-last-message')+1]).write_text(json.dumps(p))\n")
    adapter=tmp_path/'never-called.py';adapter.write_text('raise AssertionError("No delivery was requested in this fixture")\n')
    delivery=dict(closeout_a=dict(max_observed_tokens=300_000,max_seconds=300),inflight_tokens=100_000,
        cleanup_seconds=30,submit_seconds=60,confirm_seconds=60,
        adapter=dict(adapter=str(adapter),launcher_argv=[sys.executable,str(adapter)],config={},target={},inputs=[],source_roots=[str(public)]))
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable,str(launcher)],max_tokens=tokens,max_seconds=seconds,max_steps=steps,
        model='fixture',reasoning_effort='high',search_policy=None,node_budget_mode=mode,
        node_budget_policy={role:dict(max_observed_tokens=2_000_000,max_seconds=900) for role in 'HWAE'},delivery_policy=delivery)
    stamp=budget.clock_stamp()
    stamp['at_unix']-=spent;stamp['at_boottime']-=spent
    c.write(root/'host-started.json',stamp)
    original_stamp=budget.clock_stamp
    try:
        budget.clock_stamp=lambda:stamp
        budget.transition(root,'advance','fixture consumption')
    finally:
        budget.clock_stamp=original_stamp
    return root


def prefix(root):
    finish(root,1,'H',[],action='experiment')
    c.build_job(root,2,'W',[1],instruction='Exact scientific question');finish(root,2,'W',[1])
    finish(root,3,'A',[1,2],action='continue_w')
    c.build_job(root,4,'W',[1,2,3],instruction='Exact scientific question');finish(root,4,'W',[1,2,3])
    finish(root,5,'A',[1,4],action='continue_w')


def test_continue_w_uses_positive_shared_time_below_its_soft_target(tmp_path,monkeypatch):
    root=fixture(tmp_path,seconds=60);prefix(root)
    assert c.validate(c.workspace(root,5))['next_action']=='continue_w'
    assert 0<budget.snapshot(root)['seconds_remaining']<=60
    assert c.settings(root)['node_budget_policy']['W']['max_seconds']==900
    assert budget.delivery_reserve(c.settings(root))==dict(tokens=0,seconds=0.,steps=0)
    assert budget.exploration_admission(root,dict(step=6,role='W')) is None
    real_execute=native.execute_native
    def execute(root,step):
        assert step==6
        real_execute(root,step)
        (root/'pause.request').write_text('Operator pause after accepted W completed')
    monkeypatch.setattr(native,'execute_native',execute)
    result=controller.run(root)
    assert result['status']=='paused' and result['completed_steps']==6, result
    assert budget.closeout_state(root)['begin'] is None and not budget.closeout_state(root)['actions']
    assert c.validate(c.workspace(root,6))['summary']=='W completed'
    hint=c.read(c.workspace(root,6)/'work/budget-status.json')
    assert 0<hint['current_work_active_seconds_available']<=60
    assert hint['metering'] in {'observed_lower_bound','inflight_unknown'}
    assert native.usage(root)['observed_tokens']==60
    assert not (root/'jobs/007.json').exists()


@pytest.mark.parametrize('dimension',['tokens','seconds'])
def test_soft_target_admission_still_refuses_exhausted_shared_budget(tmp_path,monkeypatch,dimension):
    now=[100.]
    monkeypatch.setattr(budget.time,'time',lambda:now[0])
    monkeypatch.setattr(budget.time,'clock_gettime',lambda _:now[0])
    root=fixture(tmp_path,tokens=11,seconds=60)
    finish(root,1,'H',[],action='analyze')
    job=c.build_job(root,2,'A',[1])
    assert native.usage(root)['observed_tokens']==10
    assert budget.node_admission(root,job) is None  # One actual token remains; the target is 2M.
    if dimension=='tokens':
        budget.amend(root,max_tokens=10,authorization_note='Retain consumption under this finite revised ceiling')
    else:
        now[0]+=60.
    reason='node_budget_unavailable_'+dimension
    ledger={path:path.read_bytes() for path in (root/'control-ledger').glob('*.json')}
    originals={path:path.read_bytes() for path in (root/'settings.json',root/'jobs/001.json',root/'jobs/002.json',
        root/'native-evidence/001/science-output.json',root/'native-evidence/001/result-identity.json',root/'native-evidence/001/usage.json')}
    monkeypatch.setattr(native.subprocess,'Popen',lambda *a,**k:pytest.fail('Exhausted shared budget must refuse before Popen'))
    assert budget.node_admission(root,job)==reason
    with pytest.raises(budget.NodeBudgetUnavailable,match=reason):
        native.execute_native(root,2)
    assert ledger=={path:path.read_bytes() for path in (root/'control-ledger').glob('*.json')}
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    record=c.host_record(c.workspace(root,2))
    assert not (record/'native-intent.json').exists() and not (record/'native-process.json').exists()
    assert not (record/'science-output.json').exists()
    assert budget.final_a_barrier(root) is None and budget.closeout_state(root)['begin'] is None


def test_known_failed_resume_does_not_require_unused_soft_target(tmp_path):
    from test_soft_node_budget import scene
    from test_worker_resume import wait_runner
    root=scene(tmp_path,mode='known_failure',tokens=1000,seconds=900,total_tokens=100,total_seconds=30,resume=True,sleep=0)
    assert controller.run(root)['status']=='native_stopped'
    first=wait_runner(root)
    originals={path:path.read_bytes() for path in first.iterdir() if path.is_file()}
    assert native.usage(root)['observed_tokens']==15
    assert controller.worker_resume(root,1)['status']=='wait'
    assert native.usage(root)['observed_tokens']==30 and budget.used_steps(root)==2
    assert all(path.read_bytes()==value for path,value in originals.items())


@pytest.mark.process_e2e
def test_ordinary_audit_refuses_exhausted_shared_time_without_external_effects(tmp_path,monkeypatch):
    from scidiscovery.research_loop import candidate_audit, delivery
    from test_candidate_audit import audit_fixture, request
    now=[100.]
    monkeypatch.setattr(budget.time,'time',lambda:now[0])
    monkeypatch.setattr(budget.time,'clock_gettime',lambda _:now[0])
    root=audit_fixture(tmp_path)
    job,payload=request(root)
    assert not job.get('purpose') and not job.get('final_only')
    assert budget.delivery_reserve(c.settings(root))==dict(tokens=0,seconds=0.,steps=0)
    now[0]+=400.
    assert budget.snapshot(root)['seconds_remaining']==0
    originals={path:path.read_bytes() for path in (root/'settings.json',root/'jobs/003.json',
        root/'native-evidence/003/science-output.json',root/'native-evidence/003/result-identity.json')}
    ledger={path:path.read_bytes() for path in (root/'control-ledger').glob('*.json')}
    used=budget.used_steps(root);meter=native.usage(root)
    def unexpected(*a,**k):pytest.fail('Refused audit must not resolve or invoke external work')
    monkeypatch.setattr(delivery,'freeze_sources',unexpected)
    monkeypatch.setattr(delivery,'invoke',unexpected)
    monkeypatch.setattr(native.subprocess,'Popen',unexpected)
    assert candidate_audit.execute(root,job,payload) is None
    directory=candidate_audit._directory(root,3)
    receipt=c.read(directory/'outcome.json')
    assert receipt['accepted'] is False
    assert receipt['result']['status']=='unavailable' and receipt['result']['note']=='candidate_audit_reserve_unavailable'
    original=(directory/'outcome.json').read_bytes()
    assert candidate_audit.execute(root,job,payload) is None
    assert (directory/'outcome.json').read_bytes()==original
    assert not (directory/'intent.json').exists() and not list(candidate_audit.facts(root))
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    assert ledger=={path:path.read_bytes() for path in (root/'control-ledger').glob('*.json')}
    assert budget.used_steps(root)==used and native.usage(root)==meter
    assert budget.snapshot(root)['advance_seconds']==400.
    assert not (root/'runs/submitted.json').exists() and not (root/'jobs/004.json').exists()
    assert not list((root/'submission').iterdir())
    assert budget.closeout_state(root)['begin'] is None and budget.closeout_state(root)['actions']==[]
