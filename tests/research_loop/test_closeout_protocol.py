"""Protocol 17 closeout through CLI, native processes and isolated adapters."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scidiscovery.research_loop import budget, candidate_audit, cli, contracts as c, controller, delivery
from test_delivery import FAKE_HARNESS, descriptor, delivery_policy
from test_history_handoff import finish


NATIVE = r'''import json,os,pathlib,sys
args=sys.argv[1:];prompt=sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
root=pathlib.Path(os.environ['THIN_HWA_JOB_MANIFEST']).parents[1]
(work/'work/native-prompt').write_text(prompt)
job=json.loads((work/'science-task.json').read_text())
assert job['role']=='A'
manifest=json.loads(pathlib.Path(os.environ['THIN_HWA_JOB_MANIFEST']).read_text())
index=manifest['step']-FIRST_STEP
item=SCENARIO[index]
with (work/'work/launches').open('a') as f:f.write('resume\n' if 'resume' in args else 'exec\n')
thread='11111111-2222-4333-8444-555555555555'
print(json.dumps(dict(type='thread.started',thread_id=thread)),flush=True)
home=pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME'])
if item.get('crash') and 'resume' not in args:
 p=home/'sessions/rollout.jsonl';p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
 (work/'work/checkpoint').write_text('retained original task')
 print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))),flush=True)
 sys.exit(7)
if 'resume' in args:
 assert thread in args and (work/'work/checkpoint').read_text()=='retained original task'
if item.get('candidate') is not None and 'closeout' in job and 'catalogue' in job['closeout']['sources']:
 catalogue=json.loads((work/job['closeout']['sources']['catalogue']).read_text())
 assert any(r['candidate_id']==item['candidate'] for r in catalogue['items'])
payload=dict(summary='A bounded choice',scientific_result='Exact public evidence; target performance remains unknown.',
 limitations=item.get('limitations',['Transport fixture; target performance unasserted']),implementation_selection=None,search_review=None,
 progress_assessment=None,decision_request=None,portfolio_review=None,
 next_action=item['action'],candidate_recommendation=item.get('candidate'),route_reason='Scientific selection within original authorization')
if isinstance(payload.get('candidate_recommendation'),str):payload['selection_explanation']=dict(criteria='Compare the declared public fixture evidence and retain its stated limitations.',criteria_change=dict(status='unchanged',prior_criteria=None,changes=None,evidence=None,accepted_tradeoffs=None))
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
if item.get('pause'):(root/'pause.request').touch()
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))),flush=True)
'''


def scene(tmp_path, actions, *, count=2, audit=True, selective_failure=False, max_tokens=100000, max_seconds=600,node_budget_mode='soft'):
    if shutil.which('bwrap') is None:
        pytest.skip('Actual isolated adapter required')
    root=tmp_path/'instance';public=tmp_path/'public';(public/'harness').mkdir(parents=True)
    harness=FAKE_HARNESS.replace("mode=meta['mode']", "mode='leak' if row['run_id']==1 else 'ok'") if selective_failure else FAKE_HARNESS
    for name in ('exp_cli.py','runner.py','train.csv','val.csv','meta.json'):
        path=public/'harness'/name
        if name=='exp_cli.py':path.write_text(harness)
        elif name=='meta.json':c.write(path,dict(mode='ok',control_path=str(root/'settings.json'),hidden_path=str(tmp_path/'hidden')))
        else:path.write_text('fixture public input\n')
    objective=public/'objective.md';objective.write_text('Choose a candidate for delivery while carrying scientific uncertainty.')
    launcher=tmp_path/'native.py';launcher.write_text('SCENARIO='+repr(actions)+'\nFIRST_STEP='+str(5 if count>128 else 3)+'\n'+NATIVE)
    controller.prepare(root,objective,public,[sys.executable,str(launcher)],max_tokens=max_tokens,max_seconds=max_seconds,max_steps=32,
        model='no-model',reasoning_effort='high',search_policy=None,node_budget_mode=node_budget_mode,
        resume_policy=dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn',output_repair=True),
        node_budget_policy={r:dict(max_observed_tokens=2000,max_seconds=60) for r in 'HWAE'},
        candidate_audit_policy={'version':1} if audit else None,
        history_observer=dict(adapter=str(Path(__file__).resolve().parents[2]/'plugins/cov_execution/cov_execution/observer.py'),
            config=dict(runs='runs',evaluation_key='fixture-public')),
        delivery_policy=delivery_policy(descriptor(root,public)))
    (root/'runs').mkdir();(root/'submission').mkdir()
    (root/'runs/model.bin').write_bytes(b'exact original fixture model')
    finish(root,1,'H',[],action='experiment')
    rows=[]
    def worker(step,start,end,parents):
        c.build_job(root,step,'W',parents,instruction='Exact scientific question')
        script=c.workspace(root,step)/'artifacts/train.py'
        rows.extend(dict(run_id=n,ok=True,val_score=.6,val_acc=.6,model_path='/opt/exp/runs/model.bin',script=str(script)) for n in range(start,end+1))
        (root/'runs/results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
        finish(root,step,'W',parents,files={'train.py':'def train(train_df,val_df): return None\n'})
    worker(2,1,min(count,128),[1])
    if count>128:
        finish(root,3,'H',[1,2],action='experiment')
        worker(4,129,count,[3])
    return root


def command(root, action='closeout', *extra):
    env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[2]/'src')}
    result=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.cli',action,'--root',str(root),*extra],
        text=True,capture_output=True,timeout=60,env=env)
    assert result.returncode in (0,2),result.stdout+result.stderr
    return json.loads(result.stdout)


@pytest.mark.process_e2e
@pytest.mark.parametrize('limitations,shown',[
    ([],[]),
    (['A whole caveat'],['A whole caveat']),
    ([*['Caveat '+str(n) for n in range(1,5)],'Broader shifts remain unresolved',
      'Required audited-score plateau remains unestablished'],['Caveat '+str(n) for n in range(1,5)]),
    (['长限制'*400,'Escaped caveat: "quoted"\\newline\n',*['Later '+str(n) for n in range(5)]],
     ['Escaped caveat: "quoted"\\newline\n',*['Later '+str(n) for n in range(3)]]),
])
def test_public_closeout_limitations_are_bounded_and_link_complete_original(tmp_path,limitations,shown):
    root=scene(tmp_path,[dict(action='deliver',candidate='run-1',limitations=limitations)])
    result=command(root)
    assert result['status']=='delivery_confirmed',result
    for view in (result,command(root,'status')['closeout'],command(root),command(root,'closeout','--export-only')):
        assert view['scientific_limitations']==shown
        assert view['limitations_omitted_count']==len(limitations)-len(shown)
        assert len((json.dumps(view['scientific_limitations'],ensure_ascii=False,indent=2)+'\n').encode())<=1024
        assert view['limitations_ref']==dict(path=view['scientific_output_ref'],field='limitations')
        original=c.read(Path(view['limitations_ref']['path']))
        assert original['limitations']==limitations
        assert view['limitations_ref']['field'] in original
        assert 'limitations_omitted_count' not in original and 'limitations_ref' not in original


@pytest.mark.process_e2e
def test_empty_export_has_no_scientific_limitations_original(tmp_path):
    public=tmp_path/'public';public.mkdir();goal=public/'goal';goal.write_text('Bounded export fixture.')
    root=tmp_path/'instance'
    controller.prepare(root,goal,public,['never-launched'],max_tokens=10000,max_seconds=120,max_steps=4,
        model='no-model',reasoning_effort='high',search_policy=None,
        node_budget_policy={role:dict(max_observed_tokens=1000,max_seconds=30) for role in 'HWAE'},
        delivery_policy=dict(delivery_policy(None),submit_seconds=0,confirm_seconds=0))
    result=command(root,'closeout','--export-only')
    assert result['status']=='exported_unsubmitted'
    assert result['scientific_limitations']==[] and result['limitations_omitted_count']==0
    assert result['scientific_output_ref'] is None and result['limitations_ref'] is None


@pytest.mark.process_e2e
def test_final_host_audit_failure_retains_original_without_reselection(tmp_path):
    root=scene(tmp_path,[dict(action='deliver',candidate='run-1')],selective_failure=True)
    original=(root/'settings.json').read_bytes()
    result=command(root)
    assert result['status']=='delivery_rejected',result
    actions=budget.closeout_state(root)['actions']
    assert [row['intent']['action_kind'] for row in actions]==['submit']
    assert actions[0]['outcome']['result']['failure_phase']=='audit'
    assert actions[0]['intent']['decision']['step']==3 and actions[0]['intent']['binding']['candidate_id']=='run-1'
    assert c.read(root/'jobs/003.json')['final_only']
    retained={path:path.read_bytes() for path in (root/'jobs/003.json',
        root/'native-evidence/003/science-output.json',root/'native-evidence/003/result-identity.json')}
    assert result['existing_A_recommendation']==dict(candidate_id='run-1',author_step=3)
    assert (root/'settings.json').read_bytes()==original
    leaf_originals={path:path.read_bytes() for path in (root/'host-delivery/leaf-output').glob('*.json')}
    assert sorted(c.read(path)['phase'] for path in leaf_originals)==['audit','resolve']
    assert command(root,'run')['status']=='delivery_rejected'
    assert command(root,'status')['closeout']['status']=='delivery_rejected'
    assert budget.closeout_state(root)['actions']==actions
    assert all(path.read_bytes()==raw for path,raw in retained.items())
    assert not (root/'jobs/004.json').exists() and not (root/'submission/model.pkl').exists()
    assert {path:path.read_bytes() for path in (root/'host-delivery/leaf-output').glob('*.json')}==leaf_originals


@pytest.mark.process_e2e
def test_late_candidate_is_locatable_and_resolved_without_historical_pack(tmp_path):
    root=scene(tmp_path,[dict(action='deliver',candidate='run-140')],count=140)
    result=command(root)
    assert result['status']=='delivery_confirmed',result
    job=c.read(root/'jobs/005.json');refs=job['delivery_sources']
    assert refs['catalogue'] is True and len(refs['items'])==140 and refs['omitted_count']==0
    assert refs['items'][-1]['candidate_id']=='run-140' and refs['items'][-1]['source_step']==4
    assert len(list((root/'host-delivery/resolved').glob('*.json')))==1
    assert c.read(root/'runs/submitted.json')['run_id']==140


@pytest.mark.process_e2e
def test_transient_selected_preparation_failure_is_retryable_without_new_author(tmp_path,monkeypatch):
    root=scene(tmp_path,[dict(action='deliver',candidate='run-1')])
    original=delivery.freeze_sources
    calls=[]
    def unavailable(*args,**kwargs):
        calls.append(kwargs.get('candidate_id'))
        raise delivery.DeliveryUnresolved('delivery_source_time_limit')
    monkeypatch.setattr(delivery,'freeze_sources',unavailable)
    result=controller.closeout(root)
    assert result['status']=='closeout_unresolved' and result['reason']=='delivery_source_time_limit' and calls==['run-1']
    assert budget.closeout_state(root)['begin']['mode']=='final'
    assert not budget.closeout_state(root)['actions']
    assert cli.status(root)['closeout_preparation']['last_failure']=='delivery_source_time_limit'
    original_job=(root/'jobs/003.json').read_bytes()
    monkeypatch.setattr(delivery,'freeze_sources',original)
    assert command(root,'run')['status']=='delivery_confirmed'
    assert (root/'jobs/003.json').read_bytes()==original_job
    assert (c.workspace(root,3)/'work/launches').read_text()=='exec\n'


@pytest.mark.process_e2e
def test_unknown_submission_never_replays_or_selects_a_new_candidate(tmp_path,monkeypatch):
    root=scene(tmp_path,[dict(action='deliver',candidate='run-1')])
    def unknown(root,intent,*args,**kwargs):
        return delivery._result(intent['binding'],'unknown','accepted submission has no verified outcome',cleanup_confirmed=False)
    monkeypatch.setattr(delivery,'invoke',unknown)
    assert controller.closeout(root)['status']=='delivery_unknown'
    before={str(p):p.read_bytes() for p in (root/'control-ledger').glob('*.json')}
    result=command(root,'run')
    assert result['status']=='delivery_unknown',result
    assert command(root,'status')['closeout']['status']=='delivery_unknown'
    assert len(budget.closeout_state(root)['actions'])==1
    assert before=={str(p):p.read_bytes() for p in (root/'control-ledger').glob('*.json')}
    assert (c.workspace(root,3)/'work/launches').read_text()=='exec\n'
    assert not (root/'runs/submitted.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('action',['finish','wait'])
def test_final_stop_and_wait_do_not_implicitly_authorize_delivery(tmp_path,action):
    root=scene(tmp_path,[dict(action=action,candidate='run-1')])
    result=command(root)
    expected = 'final_a_completed' if action == 'finish' else 'closeout_native_unsealed'
    assert result['status']==expected and result['goal_completion']=='not_asserted',result
    record=root/'native-evidence/003'
    assert c.read(record/'science-result.schema.json')['properties']['next_action']['enum']==['deliver','finish']
    raw=(record/'science-output.json').read_bytes()
    assert c.read(record/'science-output.json')['next_action']==action
    assert command(root,'run')['status']==expected
    assert command(root,'status')['closeout']['status']==expected
    assert (record/'science-output.json').read_bytes()==raw
    assert (record/'result-identity.json').exists() is (action == 'finish')
    assert not (record/'continuations').exists()
    assert not (root/'jobs/004.json').exists()
    assert not budget.closeout_state(root)['actions'] and not (root/'runs/submitted.json').exists()


@pytest.mark.process_e2e
def test_final_catalogue_failure_cannot_implicitly_authorize_delivery(tmp_path,monkeypatch):
    root=scene(tmp_path,[dict(action='finish',candidate=None)])
    worker_originals={path:path.read_bytes() for path in (root/'jobs/002.json',
        root/'native-evidence/002/science-output.json',root/'native-evidence/002/result-identity.json')}
    calls=[]
    def unavailable(*args,**kwargs):
        calls.append(True)
        raise delivery.DeliveryUnresolved('delivery_catalogue_capacity')
    monkeypatch.setattr(delivery,'catalogue_sources',unavailable)
    result=controller.closeout(root)
    assert result['status']=='final_a_completed' and result['reason']=='scientific_delivery_declined',result
    job=c.read(root/'jobs/003.json')
    assert job['final_only'] and 'delivery_catalogue_capacity' in job['instruction']
    assert c.read(root/'jobs/002.json')['role']=='W'
    from scidiscovery.research_loop import accepted_prefix
    prefix = job['history']['prefix']
    assert prefix['count'] == prefix['cutoff'] == 2
    assert [accepted_prefix.member(root, prefix, step)['step'] for step in (1, 2)] == [1, 2]
    assert result['submission_result']['status']=='unsubmitted' and result['submission_result']['note']==result['reason'] and calls==[True]
    assert not budget.closeout_state(root)['actions'] and not (root/'runs/submitted.json').exists()
    original=(root/'native-evidence/003/result-identity.json').read_bytes()
    assert command(root)['status']=='final_a_completed'
    assert (root/'native-evidence/003/result-identity.json').read_bytes()==original
    assert all(path.read_bytes()==raw for path,raw in worker_originals.items())
    assert not (root/'jobs/004.json').exists()


@pytest.mark.process_e2e
def test_unknown_resolver_cleanup_blocks_repreparation(tmp_path,monkeypatch):
    root=scene(tmp_path,[dict(action='deliver',candidate='run-1')])
    def unknown(*args,**kwargs):
        raise delivery.DeliveryUnresolved('delivery_cleanup_unknown')
    monkeypatch.setattr(delivery,'freeze_sources',unknown)
    assert controller.closeout(root)['reason']=='delivery_cleanup_unknown'
    assert budget.execution_blocked(root)
    assert command(root,'run')['reason']=='delivery_original_execution_unresolved'
    assert len(list((root/'host-delivery/preparation').glob('*.json')))==1
    assert not budget.closeout_state(root)['actions']
