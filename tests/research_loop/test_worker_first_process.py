"""Real Protocol/controller/bridge flow with an opaque local executor fixture."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, execution, experiments
from test_composed_execution_results import composed_registry


pytestmark=pytest.mark.process_e2e


def _failure_diagnostics(root,reply):
    """Keep only explicitly named, bounded local transport diagnostics."""
    result=dict(stdout=reply.stdout,stderr=reply.stderr,originals={})
    for step in range(1,7):
        record=root/'native-evidence'/f'{step:03d}'
        for name in ('runner.stderr','native.stderr','native-outcome.json','bridge-operation.json',
                     'science-output.json','result.pending.json','result.json'):
            path=record/name
            if path.is_symlink() or not path.is_file():continue
            with path.open('rb') as stream:
                stream.seek(0,2);length=stream.tell();stream.seek(max(0,length-8192))
                result['originals'][f'{step:03d}/{name}']=stream.read(8192).decode('utf-8',errors='replace')
        path=record/'experiment-config.json'
        if path.is_file() and not path.is_symlink() and path.stat().st_size<=65536:
            config=json.loads(path.read_text())
            # Configurations can carry authority details. Retain only the
            # concrete interface/strategy facts needed by this no-auth test.
            result['originals'][f'{step:03d}/experiment-config-summary']=dict(
                step=(config.get('scope') or {}).get('step'),role=(config.get('scope') or {}).get('role'),
                research_strategy_version=config.get('research_strategy_version'),
                executor=(config.get('execution_interface') or {}).get('executor'),
                worker_view_version=config.get('worker_view_version'),
                breadth=None if config.get('breadth') is None else dict(
                    restricted_combination=config['breadth'].get('restricted_combination'),
                    mechanisms=[dict(mechanism_ref=row['mechanism_ref'],ordinal=row['ordinal'],
                        scientific_status=row['status'],declaration_status=(row.get('research_binding') or {}).get('status'))
                        for row in config['breadth']['mechanisms']]))
        path=root/'jobs'/f'{step:03d}.json'
        if path.is_file() and not path.is_symlink() and path.stat().st_size<=256000:
            job=json.loads(path.read_text());bound=job.get('search') or {}
            result['originals'][f'{step:03d}/job-search-summary']=dict(
                role=job['role'],evidence_return=job.get('evidence_return'),
                focus=bound.get('focus'),bounded_correction=bound.get('bounded_correction'),
                mechanisms=[dict(mechanism_ref=row['mechanism_ref'],ordinal=row['ordinal'],
                    scientific_status=row['status'],declaration_status=(row.get('research_binding') or {}).get('status'))
                    for row in (bound.get('breadth') or {}).get('mechanisms',[])])
        directory=root/'host-runtime/experiment-control'/f'{step:03d}'/'execution-diagnostics'
        if directory.is_dir() and not directory.is_symlink():
            for path in sorted(directory.iterdir())[-8:]:
                if path.suffix!='.json' or path.is_symlink() or not path.is_file():continue
                with path.open('rb') as stream:
                    stream.seek(0,2);length=stream.tell();stream.seek(max(0,length-8192))
                    result['originals'][f'{step:03d}/execution-diagnostics/{path.name}']=stream.read(8192).decode('utf-8',errors='replace')
        path=c.workspace(root,step)/'work/wire-response-schema.json'
        if path.is_file() and not path.is_symlink() and path.stat().st_size<=65536:
            schema=json.loads(path.read_text())
            result['originals'][f'{step:03d}/wire-response-schema']=dict(
                properties=schema.get('properties'),required=schema.get('required'))
    destination=os.environ.get('SCID_TEST_LOG_DIR')
    if destination:
        directory=Path(destination);directory.mkdir(parents=True,exist_ok=True)
        (directory/'worker-first-process-diagnostic.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result


LAUNCHER=r'''import json,pathlib,sys
from scidiscovery.research_loop import worker_execution_bridge as bridge
CORRECTION=globals().get('CORRECTION',False)
request=json.load(sys.stdin)
assert request['protocol']=='scidiscovery-agent-v1'
work=pathlib.Path(request['workspace'])
task=json.loads((work/'science-task.json').read_text())
step=int(work.name)
properties=request['response_schema']['properties']
(work/'work/wire-response-schema.json').write_text(json.dumps(request['response_schema']))
payload={key:None for key in properties}
payload.update(summary='Deterministic W-first protocol flow',
 scientific_result='The opaque fixture returned an exact original; this is an engineering observation only.',
 limitations=['This protocol fixture supplies no scientific qualification.'],implementation_selection=None)
print(json.dumps(dict(type='session',session_id='worker-first-fixture-'+str(step))),flush=True)
def call(arguments):
 response=bridge.client(work/'work/experiment',arguments)
 assert response['code']==0,response
 return response['result']
question='Which retained input exposes the fixture observable?'
mechanism='w001.terminal' if CORRECTION else 'w001.m1'
if step in (1,3) or CORRECTION and step==5:
 assert 'stage_intent' in properties
 packet=work/'code/package.py';packet.parent.mkdir(exist_ok=True)
 packet.write_text('opaque original '+str(step))
 declaration=work/'work/declaration.json'
 if step==1:
  declaration.write_text(json.dumps(dict(declarations=[dict(action='declare',mechanism_ref='w001.m1',
   question=question,exploration=True)])))
  declared=call(['research-declare','--research',str(declaration)])
  assert declared['status']=='research_declared' and declared['research']['mechanisms'][0]['mechanism_ref']=='w001.m1'
 elif step==3:
  assert mechanism in json.dumps(task),task
  # Read the exact assigned scientific original, not a host control record.
  prior=json.loads((work/'inputs/parents/001/conclusion.json').read_text())
  assert 'This protocol fixture supplies no scientific qualification.' in json.dumps(prior)
  navigation=json.loads((work/task['lineage']['indexes']['research']).read_text())
  assert navigation['latest_w']['source_step']==1 and navigation['latest_h']['source_step']==2,navigation
  catalog=json.loads((work/task['lineage']['mechanisms']['catalog']['path']).read_text())
  retained=next(row for row in catalog['mechanisms'] if row['mechanism_ref']==mechanism)
  assert retained['minimal_test']==question,retained
 else:
  assert 'revise_h' not in properties['stage_intent']['enum'] and 'review' not in properties['stage_intent']['enum'],properties
 batch=work/'work/batch.json'
 purpose=dict(mechanism_ref=mechanism,experiment_question=dict(
  question='Does this exact opaque input retain the declared observable?',
  change_criterion='Choose a different comparison when the observable is missing; preserve the known original otherwise.'))
 if CORRECTION and step==1:
  purpose['declarations']=[dict(action='merge',mechanism_ref='w001.bridge',question='An intermediate input distinction',
   exploration=True,predecessors=['w001.m1']),dict(action='merge',mechanism_ref=mechanism,question=question,
   exploration=True,predecessors=['w001.bridge'])]
 batch.write_text(json.dumps(purpose))
 if step==5:
  catalog=json.loads((work/task['lineage']['mechanisms']['catalog']['path']).read_text())
  numbers={row['mechanism_ref']:str(row['ordinal']) for row in catalog['mechanisms']}
  denied=[]
  for retired in ('w001.m1','w001.bridge'):
   number=numbers[retired]
   refused=bridge.client(work/'work/experiment',['run','--payload',str(packet),'--new-plan','--mechanism',number])
   assert refused['code']==2 and refused['result'].get('accepted') is False,refused
   denied.append(dict(mechanism_ref=retired,number=number,result=refused['result']))
  (work/'work/correction-refusals.json').write_text(json.dumps(denied))
  result=call(['run','--payload',str(packet),'--new-plan','--mechanism',numbers[mechanism]])
 else:
  result=call(['run','--payload',str(packet),'--new-plan','--research',str(batch)])
 assert result['status']=='known_success',result
 original=result['call_id']
 queried=call(['query','--call-id',original]);assert queried['call_id']==original
 collected=call(['collect','--call-id',original]);assert collected['call_id']==original
 assert collected['outputs'],collected
 for output in collected['outputs']:
  assert pathlib.Path(output['path']).read_text()=='opaque original '+str(step)
 (work/'work/bridge-flow.json').write_text(json.dumps(dict(call_id=original,run=result,query=queried,collect=collected)))
 nominations=task.get('candidate_nomination_mechanisms') or ([dict(mechanism_ref=mechanism,status='pending')] if step in (3,5) else [])
 if nominations:
  payload['candidate_nominations']=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],
   reason='The opaque transport fixture supplies no scientifically justified candidate.') for row in nominations if row['status']!='waived'])
 payload['stage_intent']='revise_h' if step==1 else 'decision'
 if step==1:
  payload['consultation_question']='What extra distinction can separate an absent observable from an implementation failure?'
  payload['route_reason']='Retain the original observable question while consulting H.'
elif step==2:
 assert 'wait' in properties.get('next_action',{}).get('enum',[]),properties
 payload.update(scientific_result='An absent input and an implementation failure need different controls; no additional scientific claim is made.',
  next_action='wait',route_reason='Return this distinction to the requesting W without declaring new experimental work.')
else:
 assert step in (4,6) and 'finish' in properties['next_action']['enum'],properties
 payload.update(next_action='finish',candidate_recommendation=None,
  scientific_result='Decline delivery: synthetic transport observations supply no scientifically qualified candidate.',
  route_reason='Reject every candidate and preserve the explicit scientific limitations.')
 if CORRECTION and step==4:
  assert 'continue_w' in properties['next_action']['enum'],properties
  payload.update(next_action='continue_w',decision_request=dict(decision='mechanism',
   question='Does the exact declared terminal mechanism preserve the observable in one corrected input?',
   change_criterion='Retain or reject the existing terminal evidence according to this bounded observation.'))
 if CORRECTION and step==6:
  assert 'continue_w' not in properties['next_action']['enum'],properties
 catalog=json.loads((work/task['lineage']['mechanisms']['catalog']['path']).read_text())
 coverage=catalog['mechanisms']
 if coverage and payload['next_action']=='finish':
  payload['final_coverage']=[dict(mechanism_ref=row['mechanism_ref'],status='unexplored',
   reason='The fixture exercised transport only and supplies no scientific qualification.') for row in coverage]
print(json.dumps(dict(type='result',result=payload)),flush=True)
print(json.dumps(dict(type='completed',usage=dict(input_tokens=20,cached_input_tokens=0,output_tokens=5))),flush=True)
'''


def test_real_protocol_bridge_keeps_same_w_declaration_across_consultation_and_final_refusal(tmp_path,composed_registry):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Retain exact opaque originals and explicit scientific limitations.')
    launcher=tmp_path/'native-protocol.py';launcher.write_text(LAUNCHER)
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable,str(launcher)],native_backend='protocol',
        model='deterministic-protocol-fixture',reasoning_effort=None,service_tier=None,
        max_tokens=5000,max_seconds=180,max_steps=8,
        node_budget_policy={role:dict(max_observed_tokens=1000,max_seconds=30) for role in 'HWAE'},
        execution_config=dict(executor='composed_execution:fixture',configuration=dict(synchronous=True)))
    assert c.settings(root)['research_strategy_version']==3
    command=[sys.executable,'-m','scidiscovery.research_loop.cli','run','--root',str(root)]
    reply=subprocess.run(command,capture_output=True,text=True,timeout=90)
    assert reply.returncode==0,_failure_diagnostics(root,reply)
    result=json.loads(reply.stdout)
    assert result['reason']=='scientific_delivery_declined',result
    assert [c.read(root/'jobs'/f'{step:03d}.json')['role'] for step in range(1,5)]==['W','H','W','A']
    assert not (root/'jobs/005.json').exists()
    assert c.read(root/'jobs/001.json')['direct_refs']==[]
    assert c.read(root/'jobs/002.json')['purpose']=='worker_consultation'
    assert c.read(root/'jobs/004.json')['evidence_return']['kind']=='delivery_review'
    first=c.read(c.workspace(root,1)/'work/bridge-flow.json')
    second=c.read(c.workspace(root,3)/'work/bridge-flow.json')
    assert first['call_id']!=second['call_id']
    for step,receipt in ((1,first),(3,second)):
        config=c.read(execution.origin(root,step)/'experiment-config.json')
        records=experiments.Records(config)
        assert len(records.calls())==1
        saved=records.accepted(receipt['call_id'])['mechanism_binding']
        assert saved['mechanism_ref']=='w001.m1'
        assert saved['research_binding']['origin_ref']['source_step']==1
        assert saved['research_binding']['exploration'] is True
        assert saved['research_binding']['experiment_question']['change_criterion']
    head=accepted_budget.read(root)
    mechanism=head['search']['table']['mechanisms']['w001.m1']
    assert mechanism['accepted_calls']==2
    assert head['search']['supervision']['investment']['total_calls']==2
    assert not budget.closeout_state(root)['actions']
    assert not (root/'host-delivery/binding.json').exists()
    before=(execution.origin(root,4)/'result-identity.json').read_bytes()
    repeated=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert repeated.returncode==0 and json.loads(repeated.stdout)['reason']=='scientific_delivery_declined'
    assert (execution.origin(root,4)/'result-identity.json').read_bytes()==before
    assert not (root/'jobs/005.json').exists()


def test_real_protocol_bounded_correction_uses_only_the_source_w_runtime_combination(tmp_path,composed_registry):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Retain opaque originals and permit one explicitly bounded correction.')
    launcher=tmp_path/'native-protocol.py';launcher.write_text('CORRECTION=True\n'+LAUNCHER)
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable,str(launcher)],native_backend='protocol',
        model='deterministic-protocol-fixture',reasoning_effort=None,service_tier=None,
        max_tokens=5000,max_seconds=180,max_steps=8,
        node_budget_policy={role:dict(max_observed_tokens=1000,max_seconds=30) for role in 'HWAE'},
        execution_config=dict(executor='composed_execution:fixture',configuration=dict(synchronous=True)))
    command=[sys.executable,'-m','scidiscovery.research_loop.cli','run','--root',str(root)]
    reply=subprocess.run(command,capture_output=True,text=True,timeout=90)
    assert reply.returncode==0,_failure_diagnostics(root,reply)
    result=json.loads(reply.stdout)
    assert result['reason']=='scientific_delivery_declined',result
    assert [c.read(root/'jobs'/f'{step:03d}.json')['role'] for step in range(1,7)]==['W','H','W','A','W','A']
    job=c.read(root/'jobs/005.json')
    assert job['evidence_return']['kind']=='delivery_correction'
    frozen=job['search']['breadth']
    assert frozen['restricted_combination'] is True
    assert [row['mechanism_ref'] for row in frozen['mechanisms']]==['w001.terminal']
    config=c.read(execution.origin(root,5)/'experiment-config.json')
    assert 'research_strategy_version' not in config
    records=experiments.Records(config)
    assert len(records.calls())==1
    receipt=c.read(c.workspace(root,5)/'work/bridge-flow.json')
    binding=records.accepted(receipt['call_id'])['mechanism_binding']
    assert binding['mechanism_ref']=='w001.terminal' and 'research_binding' not in binding
    assert binding['source_ref']==frozen['mechanisms'][0]['source_ref']
    assert len(c.read(c.workspace(root,5)/'work/correction-refusals.json'))==2
    assert c.read(root/'jobs/006.json')['evidence_return']['corrections_used']==1
    assert not (root/'jobs/007.json').exists() and not budget.closeout_state(root)['actions']
    table=accepted_budget.read(root)['search']['table']['mechanisms']
    assert table['w001.terminal']['accepted_calls']==3
    assert table['w001.m1']['accepted_calls']==table['w001.bridge']['accepted_calls']==0


RETAINED_ONLY_LAUNCHER=r'''import json,pathlib,sys
from scidiscovery.research_loop import worker_execution_bridge as bridge
request=json.load(sys.stdin)
assert request['protocol']=='scidiscovery-agent-v1'
work=pathlib.Path(request['workspace']);step=int(work.name)
properties=request['response_schema']['properties']
(work/'work/wire-response-schema.json').write_text(json.dumps(request['response_schema']))
payload={key:None for key in properties}
payload.update(summary='Retained-only bounded reasoning fixture',
 scientific_result='Only a wording distinction was examined; no scientific experiment was performed.',
 limitations=['No experiment, model or scientific qualification.'],implementation_selection=None)
print(json.dumps(dict(type='session',session_id='retained-only-'+str(step))),flush=True)
if step in (1,3):
 assert 'stage_intent' in properties
 if step==3:
  assert 'revise_h' not in properties['stage_intent']['enum']
  script=work/'code/payload.py';script.parent.mkdir(exist_ok=True);script.write_text('must not be submitted')
  denied=bridge.client(work/'work/experiment',['run','--payload',str(script),'--new-plan'])
  assert denied['code']==2 and denied['result'].get('accepted') is False,denied
  assert denied['result']['reason']=='retained_originals_only',denied
  (work/'work/retained-only-refusal.json').write_text(json.dumps(denied))
 payload['stage_intent']='decision'
elif step==2:
 assert 'continue_w' in properties['next_action']['enum'],properties
 payload.update(next_action='continue_w',route_reason='Request one bounded interpretation clarification from retained text.',
  decision_request=dict(decision='compliance',question='Does the retained wording distinguish observation from qualification?',
   change_criterion='Clarify the existing wording when it could imply an unsupported scientific claim.'))
else:
 assert step==4 and 'continue_w' not in properties['next_action']['enum'],properties
 payload.update(next_action='finish',route_reason='Reject delivery; the bounded clarification adds no qualified candidate.',
  candidate_recommendation=None,final_coverage=[])
print(json.dumps(dict(type='result',result=payload)),flush=True)
print(json.dumps(dict(type='completed',usage=dict(input_tokens=20,cached_input_tokens=0,output_tokens=5))),flush=True)
'''


def test_real_protocol_empty_correction_reads_retained_text_and_refuses_new_execution(tmp_path,composed_registry):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Clarify retained claims without inventing scientific evidence.')
    launcher=tmp_path/'native-protocol.py';launcher.write_text(RETAINED_ONLY_LAUNCHER)
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable,str(launcher)],native_backend='protocol',
        model='deterministic-protocol-fixture',reasoning_effort=None,service_tier=None,
        max_tokens=5000,max_seconds=180,max_steps=6,
        node_budget_policy={role:dict(max_observed_tokens=1000,max_seconds=30) for role in 'HWAE'},
        execution_config=dict(executor='composed_execution:fixture',configuration=dict(synchronous=True)))
    reply=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.cli','run','--root',str(root)],
        capture_output=True,text=True,timeout=90)
    assert reply.returncode==0,_failure_diagnostics(root,reply)
    assert json.loads(reply.stdout)['reason']=='scientific_delivery_declined'
    assert [c.read(root/'jobs'/f'{step:03d}.json')['role'] for step in range(1,5)]==['W','A','W','A']
    job=c.read(root/'jobs/003.json')
    assert job['search']['bounded_correction']['mechanism_refs']==[]
    assert job['search']['breadth']['restricted_combination'] is True
    assert job['search']['breadth']['mechanisms']==[]
    assert c.read(c.workspace(root,3)/'work/retained-only-refusal.json')['result']['reason']=='retained_originals_only'
    for step in (1,3):
        config=c.read(execution.origin(root,step)/'experiment-config.json')
        assert experiments.Records(config).calls()==[]
    assert not (root/'jobs/005.json').exists() and not budget.closeout_state(root)['actions']
