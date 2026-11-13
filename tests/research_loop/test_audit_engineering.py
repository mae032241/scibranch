"""Engineering audit faults are not scientific rejection; real CLI/leaf processes."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scidiscovery.research_loop import audit_recovery, budget, candidate_audit, contracts as c, delivery
from cov_execution import delivery as cov_delivery
from test_delivery import prepare_fixture, closeout_decision, accepted
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


def cli(root,action='run',*extra):
    env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[2]/'src')+os.pathsep+os.environ.get('PYTHONPATH','')}
    done=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.cli',action,'--root',str(root),*extra],env=env,text=True,capture_output=True,timeout=120)
    assert done.returncode in (0,2),(done.stdout,done.stderr)
    return json.loads(done.stdout)


@pytest.mark.parametrize('code',sorted(cov_delivery.AUTHOR_REPAIR_CODES))
def test_worker_audit_fault_is_preserved_without_unchanged_retry(tmp_path,monkeypatch,code):
    calls=[]
    request=dict(operation_id='a'*64,candidate_id='run-1',artifact_ref='b'*64)
    def fault(spec,item,seconds,**kwargs):
        calls.append(item)
        return dict(cov_delivery._reply(dict(request,phase='audit'),'engineering_error',code),diagnostic='Original bounded diagnostic')
    monkeypatch.setattr(delivery,'_call',fault)
    result,returned,_=audit_recovery.run(tmp_path,{},None,request,5)
    assert result==dict(status='engineering_error',note=code,diagnostic='Original bounded diagnostic',cleanup_confirmed=True,recovery='author_repair')
    assert len(calls)==1 and returned==request
    assert not (tmp_path/'host-delivery/audit-recovery').exists()


def test_ordinary_unresolved_selection_still_requires_its_author(tmp_path):
    from scidiscovery.research_loop import controller
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    public=tmp_path/'public';public.mkdir();objective=public/'objective';objective.write_text('Retain exact scientific scope.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['never-call-a-model'],max_tokens=100000,max_seconds=60,max_steps=6,
        model='no-model',reasoning_effort='high',search_policy=None)
    with pytest.raises(OutputRepairRequired, match='implementation_selection'):
        finish(root,1,'H',[],action='experiment',selection=['H999-I001'])
    assert not (root/'native-evidence/001/result-identity.json').exists()
    assert not (root/'jobs/002.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault',['packing','timeout'])
def test_control_rebuilds_exact_archives_without_model_copy_or_new_science(tmp_path,monkeypatch,fault):
    root,_,nodes=prepare_fixture(tmp_path)
    try:refs=delivery.freeze_sources(root,nodes)
    except delivery.DeliveryUnresolved as error:
        from test_delivery import failure_diagnostics
        pytest.fail(str((error.code,failure_diagnostics(root))))
    _,_,bound=closeout_decision(root,refs)
    intent=accepted(root,bound);before=budget.used_steps(root)
    original=delivery._call;phases=[]
    def initial_pack_error(spec,request,seconds,**kwargs):
        phases.append(request['phase'])
        if request['phase']=='audit':assert 'target' not in request
        elif request['phase']=='promote':assert request['target']==spec['target']
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
