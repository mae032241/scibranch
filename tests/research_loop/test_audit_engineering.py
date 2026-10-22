"""Engineering audit faults are not scientific rejection; real CLI/leaf processes."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import audit_recovery, budget, candidate_audit, contracts as c, delivery, engineering
from cov_execution import delivery as cov_delivery
from scidiscovery.research_loop.experiment_client import record_directory
from test_cov_delivery_lineage import managed_scene
from test_cov_lineage import BACKEND
from test_delivery import FAKE_HARNESS, prepare_fixture, closeout_decision, accepted
from test_history_handoff import finish


@pytest.mark.parametrize('note,expected',[
    ('script /inputs/source.py not found; audit skipped',('engineering_error','audit_source_missing')),
    ('audit timed out after 45s (audit exceeded 45s budget)',('engineering_error','audit_timeout')),
    ('audit failed during training: ModuleNotFoundError: missing helper',('engineering_error','audit_dependency_missing')),
    ('audit failed during training: ValueError: broken shape',('engineering_error','audit_training_error')),
    ('audit prediction failed: RuntimeError: shape',('engineering_error','audit_prediction_error')),
    ('train.py has no train() function; audit skipped',('engineering_error','audit_interface_missing')),
    ('could not import runner.score: absent',('engineering_error','audit_harness_unavailable')),
    ('audit skipped',('unclassified','audit_not_completed_unclassified')),
])
def test_typed_incomplete_receipt_never_claims_scientific_failure(note,expected):
    assert cov_delivery.audit_status(dict(run_id=1,leakage_flag=False,audit_score=None,audit_gap=None,audit_note=note),1)==expected


@pytest.mark.parametrize('flag,status',[(False,'audited'),(True,'rejected')])
def test_only_complete_finite_audit_can_qualify_or_reject(flag,status):
    proof=dict(run_id=1,leakage_flag=flag,audit_score=.1,audit_gap=.2,audit_note='audit took .1s on held-out rows')
    assert cov_delivery.audit_status(proof,1)[0]==status
    proof['audit_score']=None
    assert cov_delivery.audit_status(proof,1)[0]=='unclassified'


NATIVE=r'''import json,os,pathlib,subprocess,sys
from cov_execution import lineage as cov_lineage
args=sys.argv[1:];sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);manifest=json.loads(pathlib.Path(os.environ['THIN_HWA_JOB_MANIFEST']).read_text())
root=pathlib.Path(os.environ['THIN_HWA_JOB_MANIFEST']).parents[1]
task=json.loads((work/'science-task.json').read_text())
payload=dict(summary='Bounded fake repair evidence',scientific_result='No scientific efficacy claim.',limitations=['Deterministic native fixture'],implementation_selection=None)
if task['role']=='W':
 thread='11111111-2222-4333-8444-555555555555'
 print(json.dumps(dict(type='thread.started',thread_id=thread)),flush=True)
 home=pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME']);session=home/'sessions/rollout.jsonl';session.parent.mkdir(parents=True,exist_ok=True)
 if not session.exists():session.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
 if FAIL_FIRST and 'resume' not in args:
  print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))),flush=True);raise SystemExit(7)
 if 'resume' in args:assert thread in args
 repair=task['engineering_repair'];assert repair['candidate_id']=='run-1'
 assert 'No module named' in repair['diagnostic'] or 'timed out' in repair['diagnostic']
 assert json.loads((work/repair['source']).read_text())['revision']=='P'
 config=work/'work/experiment-config.json'
 cov_lineage.configure(root,manifest['step'],[sys.executable,BACKEND,'--runs-root',str(root/'runs')],root/'runs',config,receipt_channel='backend_file')
 script=work/'work/fixed.py';script.write_text(json.dumps(dict(mode='success',revision='fixed')))
 base=[sys.executable,'-m','scidiscovery.research_loop.experiments','--config',str(config),'run','--script',str(script)]
 wrong=subprocess.run([*base,'--new-plan'],capture_output=True,text=True);assert wrong.returncode==2,wrong.stdout
 result=subprocess.run([*base,'--parent-plan','run-1','--change-intent','Repair the missing implementation helper'],capture_output=True,text=True)
 assert result.returncode==0,(result.stdout,result.stderr)
 assert json.loads(result.stdout)['candidate_id']=='run-3'
 again=subprocess.run([*base,'--parent-plan','run-1','--change-intent','Duplicate repair'],capture_output=True,text=True)
 assert again.returncode==2,again.stdout
 payload.update(stage_intent='review',mechanism_findings=None)
else:
 assert task['role']=='A' and 'run-3' in json.dumps(task)
 assert not (root/'submission/model.pkl').exists()
 payload.update(next_action='deliver',candidate_recommendation='run-3',route_reason='Authorize only the new repaired candidate',
  search_review=None,progress_assessment=None,decision_request=None,portfolio_review=None)
if isinstance(payload.get('candidate_recommendation'),str):payload['selection_explanation']=dict(criteria='Compare the declared public fixture evidence and retain its stated limitations.',criteria_change=dict(status='unchanged',prior_criteria=None,changes=None,evidence=None,accepted_tradeoffs=None))
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))),flush=True)
'''


def cli(root,action='run',*extra):
    env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[2]/'src')+os.pathsep+os.environ.get('PYTHONPATH','')}
    done=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.cli',action,'--root',str(root),*extra],env=env,text=True,capture_output=True,timeout=120)
    assert done.returncode in (0,2),(done.stdout,done.stderr)
    return json.loads(done.stdout)


def repair_scene(tmp_path,*,ordinary=False,reason='dependency',always_fail=False,max_tokens=100000,crash=False,output_capacity=None,audit_after_repair=False,pause_after_author=False,deliver_after_unavailable=False,initial_action=None,legacy=False,final_after_repair=False):
    backend=tmp_path/'repair-backend.py';backend.write_text(BACKEND)
    program=NATIVE
    if audit_after_repair:
        program=program.replace("next_action='deliver',candidate_recommendation='run-3'",
            "next_action='deliver' if (task.get('candidate_audit') or {}).get('status') in "+repr(('audited','unavailable') if deliver_after_unavailable else ('audited',))+" else 'audit',candidate_recommendation='run-3'")
    if pause_after_author:
        program=program.replace("pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))",
            "pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))\nif manifest['step']==5:(root/'pause.request').touch()")
    native=tmp_path/'native.py';native.write_text('FAIL_FIRST='+repr(crash)+'\nBACKEND='+repr(str(backend))+'\n'+program)
    harness=FAKE_HARNESS.replace("mode=meta['mode']", "spec=json.loads(pathlib.Path('/inputs/source.py').read_text())\nmode='skip' if spec['revision']=='P' else 'ok'")
    if always_fail:harness=harness.replace("spec['revision']=='P'","spec['revision']!='Q'")
    note='audit failed during training: ModuleNotFoundError: No module named fixture_helper' if reason=='dependency' else 'audit timed out after 45s (audit exceeded 45s budget)'
    harness=harness.replace("else 'audit skipped'",'else '+repr(note))
    with pytest.MonkeyPatch.context() as patch:
        if legacy or final_after_repair:
            from scidiscovery.research_loop import controller
            prepare=controller.prepare
            def frozen_prepare(*args,**kwargs):
                if final_after_repair:
                    # H/W/A + one ordinary audit + repaired W exhaust this grant.
                    kwargs['max_steps']=5
                value=prepare(*args,**kwargs)
                if legacy:
                    root=Path(args[0])
                    assert not list((root/'control-ledger').glob('*.json')) and not list((root/'jobs').glob('*.json'))
                    value.pop('final_a_completion_policy')
                    c.write(root/'settings.json',value)
                    c.write(root/'settings-identity.json',dict(sha256=c.digest(root/'settings.json')))
                return value
            patch.setattr(controller,'prepare',frozen_prepare)
        root,*rest=managed_scene(tmp_path,launcher=[sys.executable,str(native)],harness=harness,audit=ordinary or audit_after_repair,max_tokens=max_tokens,output_capacity=output_capacity,resume_policy=dict(max_attempts=1,protocol='codex_exec_resume',usage_scope='turn',output_repair=False) if crash else None)
    if initial_action:
        finish(root,3,'A',[1,2],action=initial_action)
    elif ordinary:
        finish(root,3,'A',[1,2],action='audit',recommendation='run-1')
    else:
        finish(root,3,'A',[1,2],action='deliver',recommendation='run-1')
    return root,rest


@pytest.mark.process_e2e
@pytest.mark.parametrize('reason',['dependency','timeout'])
def test_exact_worker_repair_creates_new_candidate_and_requires_new_authorization(tmp_path,reason):
    root,rest=repair_scene(tmp_path,ordinary=True,reason=reason)
    seals={n:(root/f'native-evidence/{n:03d}/result-identity.json').read_bytes() for n in (1,2,3)}
    result=cli(root)
    assert result['status']=='delivery_confirmed',(result,[(str(p),p.read_text()[-4000:]) for p in (root/'native-evidence').rglob('native.stderr')])
    jobs=[c.read(path) for path in sorted((root/'jobs').glob('*.json'))]
    assert [job['role'] for job in jobs]==['H','W','A','W','A','A']
    assert jobs[3]['engineering_repair']['candidate_id']=='run-1'
    assert jobs[3].get('purpose') is None and jobs[4].get('purpose') is None
    assert jobs[5]['final_only'] and not (root/'jobs/007.json').exists()
    assert len(list((record_directory(root,4)/'calls').iterdir()))==1
    assert result['last_confirmed']['candidate_id']=='run-3'
    assert (root/'submission/model.pkl').read_bytes()!=rest[-1]
    assert all((root/f'native-evidence/{n:03d}/result-identity.json').read_bytes()==value for n,value in seals.items())
    assert not list(audit_recovery.facts(root))
    before=budget.used_steps(root)
    assert cli(root)['status']=='delivery_confirmed' and budget.used_steps(root)==before
    assert cli(root,'status')['closeout']['status']=='delivery_confirmed'


@pytest.mark.process_e2e
def test_final_engineering_fault_retains_original_without_new_worker(tmp_path,monkeypatch):
    program=NATIVE.replace("assert task['role']=='A' and 'run-3' in json.dumps(task)",
        "assert task['role']=='A' and 'run-1' in json.dumps(task)")
    program=program.replace("next_action='deliver',candidate_recommendation='run-3'",
        "next_action='deliver',candidate_recommendation='run-1'")
    monkeypatch.setattr(sys.modules[__name__],'NATIVE',program)
    root,_=repair_scene(tmp_path)
    originals={root/f'native-evidence/{step:03d}/result-identity.json':
        (root/f'native-evidence/{step:03d}/result-identity.json').read_bytes() for step in (1,2,3)}
    result=cli(root)
    assert result['status']=='delivery_engineering_error',result
    jobs=[c.read(path) for path in sorted((root/'jobs').glob('*.json'))]
    assert [job['role'] for job in jobs]==['H','W','A','A'] and jobs[3]['final_only']
    assert all(job.get('engineering_repair') is None for job in jobs)
    actions=budget.closeout_state(root)['actions']
    assert [row['intent']['action_kind'] for row in actions]==['submit']
    assert actions[0]['intent']['decision']['step']==4
    assert actions[0]['outcome']['result']['note']=='audit_dependency_missing'
    assert not (root/'submission/model.pkl').exists()
    assert cli(root)['status']=='delivery_engineering_error' and budget.closeout_state(root)['actions']==actions
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    assert not (root/'jobs/005.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('unresolved_selection',[False,True])
def test_default_search_routes_exact_repair_with_no_active_scientific_focus(tmp_path,monkeypatch,unresolved_selection):
    from scidiscovery.research_loop import controller, search
    from cov_execution import lineage as cov_lineage
    import test_cov_delivery_lineage as managed
    prepare=controller.prepare
    def default_search(*args,**kwargs):
        kwargs.pop('search_policy',None)
        return prepare(*args,**kwargs)
    monkeypatch.setattr(controller,'prepare',default_search)
    def planned_finish(root,step,role,parents,*args,**kwargs):
        if step==1 and role=='H':
            kwargs['search_choice']=dict(scope='Original method',commitments=[dict(label='P',minimal_test='Bounded observation',existing_ref=None)],focus=dict(label='P',action='improve'))
        return finish(root,step,role,parents,*args,**kwargs)
    monkeypatch.setattr(managed,'finish',planned_finish)
    run=cov_lineage.run
    def initial_mechanism(*args,**kwargs):
        return run(*args,**dict(kwargs,mechanism='P'))
    monkeypatch.setattr(cov_lineage,'run',initial_mechanism)
    # Compact lineage keeps the complete candidate pool in its public pages.
    indexed_candidate="""assert task['role']=='A'
 pool=[];page=task['lineage']['indexes']['candidates']
 while page:
  part=json.loads((work/page).read_text());pool.extend(part['candidates']);page=part['next_page']
 repaired=[row for row in pool if row['id']=='run-3']
 assert len(repaired)==1,repaired
 assert repaired[0]['parent_candidate']=='run-1' and repaired[0]['observation_step']==6,repaired
 assert repaired[0]['derivation']=='declared' and repaired[0]['execution_status']=='known_success',repaired"""
    monkeypatch.setattr(sys.modules[__name__],'NATIVE',NATIVE.replace(
        "assert task['role']=='A' and 'run-3' in json.dumps(task)",indexed_candidate))
    root,_=repair_scene(tmp_path,ordinary=True,initial_action='revise_h')
    c.build_job(root,4,'H',[1,2,3],instruction='Exact scientific question')
    finish(root,4,'H',[1,2,3],action='analyze')
    c.build_job(root,5,'A',[4,2],instruction='Exact scientific question')
    finish(root,5,'A',[4,2],action='audit',recommendation='run-1',selection=['W002-I999'] if unresolved_selection else None)
    outcome=c.read(root/'native-evidence/005/result-identity.json')['control_outcome']
    assert outcome['selection_status']==('unresolved' if unresolved_selection else 'applied')
    assert c.settings(root)['search_policy']==search.DEFAULT_POLICY
    assert search.project(root,6)['focus'] is None
    originals={p:p.read_bytes() for p in (root/'native-evidence').glob('*/result-identity.json')}
    result=cli(root)
    assert result['status']=='delivery_confirmed',result
    repair=c.read(root/'jobs/006.json')
    assert repair['role']=='W' and repair['engineering_repair']['source']['step']==2
    assert repair.get('search') is None and repair.get('author_correction') is None
    from copy import deepcopy
    altered=deepcopy(repair);altered['engineering_repair']['candidate_id']='run-2'
    with pytest.raises(ValueError,match='exact audit diagnosis'):
        engineering.validate_job(root,altered)
    altered=deepcopy(repair);altered['search']={}
    with pytest.raises(ValueError,match='new scientific search scope'):
        engineering.validate_job(root,altered)
    assert len(list((record_directory(root,6)/'calls').iterdir()))==1
    assert not list(audit_recovery.facts(root))
    assert all(path.read_bytes()==value for path,value in originals.items())
    steps=budget.used_steps(root)
    assert cli(root)['status']=='delivery_confirmed' and budget.used_steps(root)==steps


@pytest.mark.parametrize('code',sorted(cov_delivery.AUTHOR_REPAIR_CODES))
def test_worker_audit_fault_is_preserved_without_unchanged_retry(tmp_path,monkeypatch,code):
    calls=[]
    request=dict(operation_id='a'*64,candidate_id='run-1',artifact_sha256='b'*64)
    def fault(spec,item,seconds,**kwargs):
        calls.append(item)
        return dict(cov_delivery._reply(dict(request,phase='audit'),'engineering_error',code),diagnostic='Original bounded diagnostic')
    monkeypatch.setattr(delivery,'_call',fault)
    result,returned,_=audit_recovery.run(tmp_path,{},None,request,5)
    assert result==dict(status='engineering_error',note=code,diagnostic='Original bounded diagnostic',cleanup_confirmed=True,recovery='author_repair')
    assert len(calls)==1 and returned==request
    assert not (tmp_path/'host-delivery/audit-recovery').exists()


@pytest.mark.parametrize('searched',[False,True])
def test_ordinary_unresolved_selection_still_requires_its_author(tmp_path,searched):
    from scidiscovery.research_loop import controller,search,semantic
    public=tmp_path/'public';public.mkdir();objective=public/'objective';objective.write_text('Retain exact scientific scope.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['never-call-a-model'],max_tokens=100000,max_seconds=60,max_steps=6,
        model='no-model',reasoning_effort='high',search_policy=search.DEFAULT_POLICY if searched else None)
    plan=dict(scope='Original method',commitments=[dict(label='P',minimal_test='Bounded observation',existing_ref=None)],focus=dict(label='P',action='improve'))
    job,_,_=finish(root,1,'H',[],action='experiment',search_choice=plan if searched else None,selection=['H999-I001'])
    payload=c.validate(c.workspace(root,1))
    route=semantic.effective_route(root,1,payload,job,(*controller._route(root,1,'H',payload,job,None,None),None))
    assert route[0]=='H' and route[-1]['reason']=='implementation_unresolved'
    corrected=c.build_job(root,2,route[0],route[1],instruction=route[2],author_correction=route[-1])
    assert corrected.get('engineering_repair') is None
    assert not (root/'native-evidence/002/native-intent.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault',['packing','timeout'])
def test_control_rebuilds_exact_archives_without_model_copy_or_new_science(tmp_path,monkeypatch,fault):
    root,_,nodes=prepare_fixture(tmp_path);refs=delivery.freeze_sources(root,nodes)
    _,_,bound=closeout_decision(root,refs)
    intent=accepted(root,bound);before=budget.used_steps(root)
    original=delivery._call;phases=[]
    def initial_pack_error(spec,request,seconds,**kwargs):
        phases.append(request['phase'])
        if phases==['audit'] and fault=='packing':
            (Path(request['stage'])/'inputs/source.py').unlink()
        return original(spec,request,.001 if phases==['audit'] and fault=='timeout' else seconds,**kwargs)
    monkeypatch.setattr(delivery,'_call',initial_pack_error)
    result=delivery.invoke(root,intent,20)
    assert result['status']=='confirmed',result
    assert phases==['audit','audit','promote']
    assert budget.used_steps(root)==before+1
    recovery=list(audit_recovery.facts(root));assert len(recovery)==1 and recovery[0][1]['status']=='audited'
    assert recovery[0][0]['max_seconds']<=c.settings(root)['delivery_policy']['submit_seconds']
    assert len(list((root/'jobs').glob('*.json')))==3
    assert not any(c.read(p)['kind']=='budget_amend' for p in (root/'control-ledger').glob('*.json'))


@pytest.mark.process_e2e
@pytest.mark.parametrize('ordinary',[False,True])
def test_insufficient_original_budget_stops_before_new_worker(tmp_path,monkeypatch,ordinary):
    from scidiscovery.research_loop import native,controller
    # Three sealed fixture turns consume 10 each. At this exact boundary all
    # remaining tokens belong to the subsequent A and execution floor; soft W
    # targets do not require their full 2000-token target, only positive balance.
    # Freeze the historical policy before the fixture creates any ledger facts.
    prepare=controller.prepare
    def legacy_prepare(*args,**kwargs):
        value=prepare(*args,**kwargs)
        root=Path(args[0])
        assert not list((root/'control-ledger').glob('*.json'))
        value.pop('final_a_completion_policy')
        c.write(root/'settings.json',value)
        c.write(root/'settings-identity.json',dict(sha256=c.digest(root/'settings.json')))
        return value
    with monkeypatch.context() as patch:
        patch.setattr(controller,'prepare',legacy_prepare)
        root,_=repair_scene(tmp_path,ordinary=ordinary,max_tokens=1130)
    settings=c.settings(root);meter=native.usage(root)
    reserved=settings['delivery_policy']['closeout_a']['max_observed_tokens']+budget.closeout_floor(settings)['tokens']
    assert budget.node_mode(root)=='soft'
    assert budget.snapshot(root)['max_tokens']-meter['accounted_tokens']==reserved
    result=cli(root)
    assert result['status']=='engineering_repair_budget_unavailable',result
    assert len(list((root/'jobs').glob('*.json')))==3
    assert not (root/'submission/model.pkl').exists()


@pytest.mark.process_e2e
def test_same_fault_lineage_cannot_automatically_spawn_workers_forever(tmp_path):
    root,_=repair_scene(tmp_path,ordinary=True,always_fail=True,audit_after_repair=True)
    result=cli(root)
    assert result['status']=='engineering_repair_exhausted',result
    assert len(list((root/'jobs').glob('*.json')))==5
    assert c.read(root/'native-evidence/005/science-output.json')['next_action']=='audit'
    assert not budget.closeout_state(root)['actions']
    assert len([c.read(p) for p in (root/'jobs').glob('*.json') if c.read(p).get('engineering_repair')])==1
    assert not (root/'submission/model.pkl').exists()
    assert cli(root,'status')['closeout'] is None
    assert cli(root)['status']==result['status']
    assert len(list((root/'jobs').glob('*.json')))==5


@pytest.mark.process_e2e
def test_accepted_control_recovery_without_outcome_is_never_replayed(tmp_path,monkeypatch):
    root,_,nodes=prepare_fixture(tmp_path);refs=delivery.freeze_sources(root,nodes)
    _,_,bound=closeout_decision(root,refs);intent=accepted(root,bound)
    original=delivery._call
    def pack_error(spec,request,seconds,**kwargs):
        if request['phase']=='audit' and Path(request['stage']).name!='recovery-1':
            (Path(request['stage'])/'inputs/source.py').unlink()
        return original(spec,request,seconds,**kwargs)
    monkeypatch.setattr(delivery,'_call',pack_error)
    from scidiscovery.research_loop import candidate_audit
    save=candidate_audit._save
    def crash_after_accepted(path,value):
        if path.name=='outcome.json' and 'audit-recovery' in str(path):raise RuntimeError('Injected host interruption')
        return save(path,value)
    monkeypatch.setattr(candidate_audit,'_save',crash_after_accepted)
    with pytest.raises(RuntimeError,match='Injected host interruption'):
        delivery.invoke(root,intent,20)
    assert budget.execution_blocked(root) and len(list(audit_recovery.facts(root)))==1
    before=len(list((root/'host-delivery/leaf-output').glob('*-audit-*.json')))
    assert cli(root)['status']=='delivery_unknown'
    assert cli(root)['status']=='delivery_unknown'
    assert len(list((root/'host-delivery/leaf-output').glob('*-audit-*.json')))==before
    assert not (root/'submission/model.pkl').exists()


@pytest.mark.process_e2e
def test_unselected_candidate_fault_cannot_launch_screen_repair(tmp_path,monkeypatch):
    from scidiscovery.research_loop import controller
    from test_control_selection import scene,closeout_author
    harness=FAKE_HARNESS.replace("mode=meta['mode']","mode='skip'").replace(
        "else 'audit skipped'","else 'audit failed during training: ModuleNotFoundError: No module named fixture_helper'")
    root=scene(tmp_path,harness=harness)
    monkeypatch.setattr(delivery,'leaf_phase',lambda *args,**kwargs:pytest.fail('No domain call before explicit candidate choice'))
    calls=closeout_author(monkeypatch,lambda task:dict(next_action='finish',candidate_recommendation=None,candidate_comparison=None))
    result=controller.closeout(root)
    assert result['status']=='closeout_native_unsealed',result
    assert [task['role'] for _,task in calls]==['A']
    assert not list((root/'host-delivery/selection').rglob('*.intent.json'))
    assert not (root/'jobs/005.json').exists() and not (root/'submission/model.pkl').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode',['soft'])
def test_known_unsealed_repair_worker_resumes_exact_session_without_rewriting_seals(tmp_path,monkeypatch,mode):
    from scidiscovery.research_loop import controller
    prepare=controller.prepare
    def node_mode(*args,**kwargs):
        return prepare(*args,**dict(kwargs,node_budget_mode=mode))
    monkeypatch.setattr(controller,'prepare',node_mode)
    root,_=repair_scene(tmp_path,ordinary=True,crash=True,max_tokens=3500)
    result=cli(root)
    assert result['status']=='native_stopped',result
    assert budget.closeout_state(root)['begin'] is None
    original=(root/'jobs/004.json').read_bytes()
    assert not (root/'native-evidence/004/result-identity.json').exists()
    from scidiscovery.research_loop import execution,native
    job=c.read(root/'jobs/004.json');paths=execution.records(root,4)
    limits=budget.snapshot(root);meter=native.usage(root)
    remaining=0  # Current ordinary repair resumes only with positive shared balance.
    with monkeypatch.context() as patch:
        observed=limits['max_tokens']-remaining
        assert observed-meter['output_tokens']>=meter['cached_input_tokens']
        patch.setattr(native,'usage',lambda root:dict(meter,input_tokens=observed-meter['output_tokens'],
            observed_tokens=observed,accounted_tokens=observed+meter.get('reserved_tokens',0)))
        with pytest.raises(budget.NodeBudgetUnavailable,match='worker_resume_budget_unavailable'):
            budget.accept_continuation(root,job,paths)
    assert len(execution.records(root,4))==1  # No continuation was accepted at zero balance.
    result=cli(root,'worker-resume','--step','4')
    assert result['status']=='delivery_confirmed',result
    assert (root/'jobs/004.json').read_bytes()==original
    records=execution.records(root,4)
    assert len(records)==2
    resumed=c.read(records[1]/'native-intent.json')
    assert resumed['continuation_kind']=='repair' and resumed['session']['thread_id']=='11111111-2222-4333-8444-555555555555'
    seal=(root/'native-evidence/004/result-identity.json').read_bytes()
    assert cli(root,'worker-resume','--step','4')['status']=='delivery_confirmed'
    assert len(execution.records(root,4))==2 and (root/'native-evidence/004/result-identity.json').read_bytes()==seal


@pytest.mark.process_e2e
@pytest.mark.parametrize('raw',['{not json','[]','null'])
def test_exit_zero_with_malformed_or_nondict_proof_stays_unclassified_without_retry(tmp_path,raw):
    harness=FAKE_HARNESS.replace("(runs/'submitted.json').write_text(json.dumps(fact))",
        "(runs/'submitted.json').write_text("+repr(raw)+")")
    root,nodes,*_=managed_scene(tmp_path,harness=harness)
    refs=delivery.freeze_sources(root,nodes)
    _,_,bound=closeout_decision(root,refs)
    intent=accepted(root,bound)
    result=delivery.invoke(root,intent,20)
    assert result['status']=='unclassified' and result['note']=='audit_receipt_invalid'
    assert not list(audit_recovery.facts(root))
    audits=list((root/'host-delivery/leaf-output').glob('*-audit-*.json'))
    assert len(audits)==1 and c.read(audits[0])['returncode']==0
    budget.action_outcome(root,intent,result)
    assert not budget.execution_blocked(root)
    assert cli(root)['status']=='delivery_unclassified'
    status=cli(root,'status')['closeout']
    assert status['status']=='delivery_unclassified' and status['current_actual'] is None
    assert not (root/'submission/model.pkl').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('damage',['changed','missing'])
def test_completed_rejection_requires_the_exact_audited_artifact(tmp_path,damage):
    changed="(runs/'submitted_model.pkl').write_bytes(b'different model')" if damage=='changed' else "(runs/'submitted_model.pkl').unlink()"
    harness=FAKE_HARNESS.replace("mode=meta['mode']","mode='leak'\n"+changed)
    root,nodes,*_=managed_scene(tmp_path,harness=harness)
    refs=delivery.freeze_sources(root,nodes)
    _,_,bound=closeout_decision(root,refs);intent=accepted(root,bound)
    result=delivery.invoke(root,intent,20)
    assert result['status']=='unclassified' and result['note']=='audit_model_identity_changed'
    assert not list(audit_recovery.facts(root)) and not (root/'submission/model.pkl').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('cause',['unclassified','control_environment'])
def test_catalogue_does_not_interpret_faults_of_unselected_candidates(tmp_path,monkeypatch,cause):
    from scidiscovery.research_loop import controller
    from test_control_selection import scene,closeout_author
    note='audit skipped' if cause=='unclassified' else 'could not import runner.score: missing control module'
    harness=FAKE_HARNESS.replace("mode=meta['mode']","mode='skip'").replace("else 'audit skipped'",'else '+repr(note))
    root=scene(tmp_path,harness=harness)
    monkeypatch.setattr(delivery,'leaf_phase',lambda *args,**kwargs:pytest.fail('Catalogue cannot invoke an unselected audit'))
    closeout_author(monkeypatch,lambda task:dict(next_action='finish',candidate_recommendation=None,candidate_comparison=None))
    result=controller.closeout(root)
    assert result['status']=='closeout_native_unsealed',result
    assert cli(root,'status')['closeout']['status']==cli(root)['status']==result['status']
    assert len(list((root/'jobs').glob('*.json')))==4 and not (root/'submission/model.pkl').exists()


@pytest.mark.process_e2e
def test_original_managed_output_capacity_blocks_initial_seal_before_repair(tmp_path,monkeypatch):
    import test_cov_delivery_lineage as managed
    original_finish=managed.finish
    scientific_output=('candidate,public_validation,limitation\n'
        'run-1,0.5,synthetic observation with unresolved target applicability\n'
        'run-2,0.5,synthetic observation with unresolved target applicability\n')
    def with_scientific_output(root,step,role,parents,**kwargs):
        if step==2 and role=='W':
            kwargs['files']=dict(kwargs.get('files',{}),**{'comparison.csv':scientific_output})
        return original_finish(root,step,role,parents,**kwargs)
    monkeypatch.setattr(managed,'finish',with_scientific_output)
    # Private execution receipts are control records. An actual scientific
    # artifact must exceed the quota before any A audit or repair is admitted.
    with pytest.raises(ValueError,match='exceeds item byte budget'):
        repair_scene(tmp_path,output_capacity=64)
    root=tmp_path/'instance';work=c.workspace(root,2)
    policy=c.settings(root)
    assert policy['max_output_item_bytes']==policy['max_output_total_bytes']==64
    assert [c.read(p)['role'] for p in sorted((root/'jobs').glob('*.json'))]==['H','W']
    assert (root/'native-evidence/001/result-identity.json').is_file()
    assert not (root/'native-evidence/002/result-identity.json').exists()
    calls=record_directory(root,2)/'calls'
    observations=[c.read(p) for p in calls.glob('*/execution-observation.json')]
    assert len(observations)==2
    outputs=[item for observation in observations for item in observation['outputs']]
    artifact=work/'artifacts/comparison.csv'
    assert artifact.read_text()==scientific_output and artifact.stat().st_size>64
    assert all(Path(item['path']).stat().st_size==item['size_bytes'] and c.digest(Path(item['path']))==item['sha256'] for item in outputs)
    originals={p:c.digest(p) for p in calls.rglob('*') if p.is_file()}
    artifact_digest=c.digest(artifact)
    with pytest.raises(ValueError,match='exceeds item byte budget'):
        c.collect_materials(root,2)
    assert originals=={p:c.digest(p) for p in calls.rglob('*') if p.is_file()}
    assert c.digest(artifact)==artifact_digest
    assert not (root/'native-evidence/004/native-intent.json').exists()
    assert not (root/'submission/model.pkl').exists()


@pytest.mark.process_e2e
def test_ordinary_repair_then_one_final_author_uses_host_audit_and_resumes(tmp_path):
    root,_=repair_scene(tmp_path,ordinary=True,final_after_repair=True)
    originals={root/f'native-evidence/{step:03d}/result-identity.json':
        (root/f'native-evidence/{step:03d}/result-identity.json').read_bytes() for step in (1,2,3)}
    result=cli(root)
    assert result['status']=='delivery_confirmed',result
    jobs=[c.read(path) for path in sorted((root/'jobs').glob('*.json'))]
    assert [job['role'] for job in jobs]==['H','W','A','W','A']
    assert all(job.get('purpose') is None for job in jobs[:4]) and jobs[4]['final_only']
    assert c.read(root/'native-evidence/005/science-output.json')['next_action']=='deliver'
    assert candidate_audit._outcome(root,5) is None
    assert not candidate_audit._directory(root,5).exists()
    actions=budget.closeout_state(root)['actions']
    assert [row['intent']['action_kind'] for row in actions]==['submit','confirm']
    assert all(row['intent']['decision']['step']==5 and row['intent']['binding']['candidate_id']=='run-3' for row in actions)
    phases=[c.read(path)['phase'] for path in (root/'host-delivery/leaf-output').glob(actions[0]['intent']['operation_id']+'-*.json')]
    assert sorted(phases)==['audit','confirm','promote']
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    steps=budget.used_steps(root)
    assert cli(root)['status']=='delivery_confirmed' and budget.used_steps(root)==steps
    assert cli(root,'status')['closeout']['status']=='delivery_confirmed'
    assert not (root/'jobs/006.json').exists()
    assert all(path.read_bytes()==raw for path,raw in originals.items())


def _pending_repaired_audit(tmp_path,*,deliver_after_unavailable=False):
    # Retained pre-FINAL audit originals need the historical policy frozen before ledger creation.
    root,_=repair_scene(tmp_path,audit_after_repair=True,pause_after_author=True,deliver_after_unavailable=deliver_after_unavailable,legacy=True)
    assert cli(root)['status']=='paused'
    (root/'pause.request').unlink()
    job=c.read(root/'jobs/005.json');payload=c.validate(c.workspace(root,5))
    assert payload['next_action']=='audit'
    return root,job,payload


def _legacy_preflight(root,job):
    # Exact three originals left by c5790ba's guard before staging/Popen.
    directory=candidate_audit._directory(root,job['step'])
    decision=candidate_audit._decision(root,job['step'])
    operation=candidate_audit._operation(root,decision,'run-3')
    intent=dict(action_id=operation,action_kind='audit',operation_id=operation,decision=decision,candidate_id='run-3',
        max_seconds=c.settings(root)['delivery_policy']['submit_seconds'],advance_seconds=budget.snapshot(root)['advance_seconds'])
    refs=delivery.selected_sources(root,job['delivery_sources'],'run-3',prepare=True)
    rows=[row for row in refs['items'] if row['candidate_id']=='run-3' and row['status']=='audit_pending']
    assert len(rows)==1
    binding=dict(candidate_id='run-3',decision=decision,source=rows[0],target=delivery._adapter(root)['target'],operation_id=operation)
    for name,value in [('intent.json',intent),('sources.json',refs),('binding.json',binding)]:candidate_audit._save(directory/name,value)
    assert job['delivery_sources']!=budget.closeout_state(root)['begin']['delivery_sources']
    return directory,intent,binding


@pytest.mark.process_e2e
def test_invalid_closeout_source_preflight_does_not_accept_an_intent(tmp_path,monkeypatch):
    root,job,payload=_pending_repaired_audit(tmp_path)
    before=budget.used_steps(root)
    with monkeypatch.context() as patch:
        patch.setattr(engineering,'sources',lambda root,step:budget.closeout_state(root)['begin']['delivery_sources'])
        with pytest.raises(ValueError,match='Candidate audit closeout boundary changed'):
            candidate_audit.execute(root,job,payload)
    assert not candidate_audit._directory(root,5).exists() and budget.used_steps(root)==before
    assert cli(root)['status']=='delivery_confirmed'


@pytest.mark.process_e2e
@pytest.mark.parametrize('partial_result',[False,True])
def test_legacy_preflight_settles_without_replaying_old_audit_or_rewriting_science(tmp_path,partial_result):
    root,job,payload=_pending_repaired_audit(tmp_path,deliver_after_unavailable=True)
    directory,intent,binding=_legacy_preflight(root,job)
    originals={path:path.read_bytes() for path in [*directory.iterdir(),root/'jobs/005.json',root/'native-evidence/005/result-identity.json']}
    before=budget.used_steps(root)
    if partial_result:
        candidate_audit._save(directory/'adapter-result.json',dict(status='unavailable',note='candidate_audit_legacy_preflight_not_dispatched',cleanup_confirmed=True,
            operation_id=intent['operation_id'],candidate_id='run-3',artifact_sha256=binding['source']['objects']['artifact']['sha256']))
        originals[directory/'adapter-result.json']=(directory/'adapter-result.json').read_bytes()
    snapshot={p.name:p.read_bytes() for p in directory.iterdir()}
    assert cli(root,'status')['closeout']['status']=='candidate_audit_unknown'
    assert {p.name:p.read_bytes() for p in directory.iterdir()}==snapshot  # Queries never settle.
    result=cli(root)
    expected='delivery_confirmed'
    assert result['status']==expected,result
    assert all(path.read_bytes()==data for path,data in originals.items())
    old=candidate_audit._outcome(root,5)
    assert old['result']['status']=='unavailable' and old['process_sha256'] is None and old['leaf_output']==[]
    assert candidate_audit.public_receipt(root,candidate_audit.receipt_ref(root,5))['qualification']=='not_completed'
    assert not (directory/'dispatch.json').exists() and not list(delivery.leaf_output(root).glob(intent['operation_id']+'-*'))
    assert c.read(root/'native-evidence/006/science-output.json')['next_action']=='deliver'
    assert candidate_audit._outcome(root,6) is None
    actions=[row for row in budget.closeout_state(root)['actions'] if row['intent']['decision']['step']==6]
    assert [row['intent']['action_kind'] for row in actions]==['submit','confirm']
    assert all(row['intent']['binding']['candidate_id']=='run-3' for row in actions)
    phases=[c.read(path)['phase'] for path in delivery.leaf_output(root).glob(actions[0]['intent']['operation_id']+'-*.json')]
    assert sorted(phases)==['audit','confirm','promote']
    assert (root/'submission/model.pkl').is_file() and not (root/'jobs/007.json').exists()
    assert budget.used_steps(root)==before+3  # One new A and its two actions; old costs remain consumed.
    after=budget.used_steps(root)
    assert cli(root)['status']==expected and budget.used_steps(root)==after


@pytest.mark.process_e2e
@pytest.mark.parametrize('evidence',['dispatch','process','leaf','recovery'])
def test_legacy_shaped_audit_with_execution_evidence_remains_unknown_without_replay(tmp_path,evidence):
    root,job,payload=_pending_repaired_audit(tmp_path)
    directory,intent,binding=_legacy_preflight(root,job)
    if evidence=='dispatch':candidate_audit._save(directory/'dispatch.json',candidate_audit._dispatch_binding(root,intent))
    elif evidence=='process':candidate_audit._save(directory/'process.json',dict(phase='audit'))
    elif evidence=='leaf':
        candidate_audit._save(delivery.leaf_output(root)/(intent['operation_id']+'-audit-1.json'),dict(preserved_execution_evidence=True))
    else:
        candidate_audit._save(root/'host-delivery/audit-recovery'/intent['operation_id']/'intent.json',dict(
            settings_sha256=c.digest(root/'settings.json'),operation_id=intent['operation_id'],attempt=1,
            reason='audit_source_missing',max_seconds=intent['max_seconds'],advance_seconds=intent['advance_seconds']))
    original={p:p.read_bytes() for p in directory.iterdir()}
    before=budget.used_steps(root)
    assert cli(root,'status')['closeout']['status']=='candidate_audit_unknown'
    for _ in range(2):assert cli(root)['status']=='candidate_audit_unknown'
    assert not (directory/'outcome.json').exists() and not (directory/'adapter-result.json').exists()
    assert all(p.read_bytes()==data for p,data in original.items()) and budget.used_steps(root)==before
