"""Real CLI and exact managed originals across W handoff; native science is synthetic."""
import json
import subprocess

import pytest

from scidiscovery.research_loop import accepted_budget, contracts as c, execution
from execution_fixtures import execution_registry
import test_default_cli_output_repair as transport


pytestmark=pytest.mark.process_e2e


QUESTION='Does the retained scientific direction address the target condition, or should it be finalized?'


def continuation_progress(root):
    """At most 60 KiB from the three exact continuation/assessment slots."""
    observed={}
    for number in ('004','005','006'):
        for relative,limit,tail in ((f'jobs/{number}.json',4096,False),
                (f'native-evidence/{number}/native-outcome.json',8192,False),
                (f'native-evidence/{number}/native.stderr',8192,True)):
            try:
                with (root/relative).open('rb') as stream:
                    if tail:
                        stream.seek(0,2)
                        stream.seek(max(0,stream.tell()-limit))
                    observed[relative]=stream.read(limit).decode('utf-8',errors='replace')
            except FileNotFoundError:
                observed[relative]='missing'
            except OSError as error:
                observed[relative]=type(error).__name__
    return observed


INJECTION=r'''
if role=='H':
 payload['search_plan']['focus']=dict(index=1,action='improve')
elif role=='W':
 import subprocess
 from scidiscovery.research_loop.worker_execution_bridge import SUBMISSION_DIRECTORY
 step=int(work.name)
 assert step in (2,3),step
 bridge=[sys.executable,'-m','scidiscovery.research_loop.worker_execution_bridge',
  '--submission-directory',str(work/SUBMISSION_DIRECTORY)]
 directory=work/'experiments/direction-second-batch';directory.mkdir(parents=True)
 script=work/'code/direction-second-batch.py'
 declared=dict(version=1,metrics=[],outputs=[dict(path='artifacts/direction-observation.txt')])
 script.write_text('from pathlib import Path\nPath("artifacts").mkdir(exist_ok=True)\nPath("artifacts/direction-observation.txt").write_text('+repr('Synthetic target observation from W'+str(step))+')\nPath("artifacts/result.json").write_text('+repr(json.dumps(declared))+')\n')
 completed=subprocess.run([*bridge,'run','--experiment-dir',str(directory),'--script',str(script),'--mechanism','1'],
  capture_output=True,text=True)
 assert completed.returncode==0,(completed.stdout,completed.stderr)
 second=json.loads(completed.stdout)
 assert second['status']=='known_success' and second['call_id']!=locator['call_id'],second
 payload.update(stage_intent='handoff',route_reason=QUESTION,
  exploration_rounds=[dict(mechanism='h001.m1',question='First explicit target-condition batch in W'+str(step),
   call_ids=[locator['call_id']],finding='A synthetic retained original; target effectiveness is unqualified.'),
   dict(mechanism='h001.m1',question='A distinct second target-condition batch in W'+str(step),
    call_ids=[second['call_id']],finding='Another synthetic retained original; no scientific qualification is inferred.')])
 (work/'work/direction-round-calls.json').write_text(json.dumps([locator['call_id'],second['call_id']]))
elif role=='A':
 task=json.loads((work/'science-task.json').read_text())
 frozen=task['supervision_request']
 assert frozen['automatic'] is True and frozen['cutoff']['total_rounds']==4,frozen
 assert frozen['cutoff']['source_step']==3 and frozen['final'] is False,frozen
 assert QUESTION in json.dumps(task)
 payload.update(supervision_result='finalize',next_action='finish',candidate_recommendation=None,
  scientific_result='The synthetic target observations do not establish suitability; finalize this bounded fixture with the evidence limitation.',
  limitations=['Mechanical CLI/identity fixture only; no model, scientific quality or execution permission is qualified.'])
 payload.pop('portfolio_review',None)
 payload.pop('final_coverage',None)
'''.replace('QUESTION',repr(QUESTION))


def test_cross_worker_rounds_insert_one_independent_a_and_cold_resume_preserves_originals(tmp_path,monkeypatch,execution_registry):
    marker="if not (config['fault']=='completed-missing-output' and role=='W' and not resume):"
    assert marker in transport.NATIVE
    monkeypatch.setattr(transport,'NATIVE',transport.NATIVE.replace(marker,INJECTION+'\n'+marker,1))
    root,_,env=transport.prepare_cli(tmp_path,fault='completed-blocked',max_tokens=10000,max_seconds=240)
    result=transport.cli(root,'run',env=env)
    assert result['status']=='finish',transport.nomination_diagnostic(root,result,fault='direction-supervision')
    jobs=[c.read(root/'jobs'/f'{step:03d}.json') for step in range(1,5)]
    assert [job['role'] for job in jobs]==['H','W','W','A']
    assert all(job.get('purpose') is None for job in jobs)
    assert all(job.get('supervision_request') is None for job in jobs[:3])
    frozen=jobs[3]['supervision_request']
    assert frozen['automatic'] is True and frozen['due_reasons']==['consecutive_rounds']
    assert frozen['cutoff']['total_rounds']==4 and frozen['cutoff']['source_step']==3
    call_ids=[]
    for step in (2,3):
        original,=execution.records(root,step)
        payload=c.read(original/'science-output.json')
        assert payload['stage_intent']=='handoff' and payload['route_reason']==QUESTION
        assert len(payload['exploration_rounds'])==2
        call_ids.extend(c.read(c.workspace(root,step)/'work/direction-round-calls.json'))
    assert len(set(call_ids))==4
    head=accepted_budget.read(root)
    state=head['search']['supervision']
    assert state['total_rounds']==state['covered_rounds']==4
    assert state['automatic_reviews']==1
    assert state['last_covered_cutoff']['assessment_step']==4
    originals=[root/'jobs'/f'{step:03d}.json' for step in range(1,5)]
    originals.extend([execution.record(root,step)/name for step in (2,3,4)
        for name in ('native-intent.json','science-output.json','result-identity.json')])
    originals.append(root/'host-history/controller-route.json')
    original_bytes={path:path.read_bytes() for path in originals}
    assert transport.cli(root,'run',env=env)['status']=='finish'
    assert all(path.read_bytes()==value for path,value in original_bytes.items())
    assert not (root/'jobs/005.json').exists()
    assert all(len(execution.records(root,step))==1 for step in (2,3,4))


def test_continue_existing_returns_fresh_w_without_a_fabricated_gap_and_agents_read_public_counters(
        tmp_path,monkeypatch,execution_registry):
    visibility=r'''
if role in {'W','A'}:
 task=json.loads((work/'science-task.json').read_text())
 research=json.loads((work/task['lineage']['indexes']['research']).read_text())
 visible=research['direction_supervision']
 step=int(work.name)
 expected={2:(0,0,0),3:(2,0,0),4:(4,0,0),5:(4,4,1),6:(6,4,1)}[step]
 assert (visible['total_rounds'],visible['covered_rounds'],visible['automatic_reviews'])==expected,visible
 if step>=5:
  assert visible['last_covered_cutoff']['total_rounds']==4,visible
  assert visible['last_covered_cutoff']['source_step']==3,visible
  assert visible['last_covered_cutoff']['assessment_step']==4,visible
 (work/'work/direction-visible-counters.json').write_text(json.dumps(visible))
'''
    worker=INJECTION.split("elif role=='A':",1)[0]
    assert 'assert step in (2,3),step' in worker
    worker=worker.replace('assert step in (2,3),step','assert step in (2,3,5),step',1)
    assert "payload.update(stage_intent='handoff',route_reason=" in worker
    worker=worker.replace("payload.update(stage_intent='handoff',route_reason=",
        "payload.update(stage_intent='decision' if step==5 else 'handoff',route_reason=",1)
    assessment=r'''
elif role=='A':
 task=json.loads((work/'science-task.json').read_text())
 step=int(work.name)
 assert QUESTION in json.dumps(task)
 payload.update(candidate_recommendation=None)
 payload.pop('search_review',None)
 payload.pop('portfolio_review',None)
 payload.pop('final_coverage',None)
 if step==4:
  frozen=task['supervision_request']
  assert frozen['automatic'] is True and frozen['cutoff']['total_rounds']==4,frozen
  payload.update(supervision_result='continue_existing',next_action='continue_w',
   route_reason='Continue the same bounded target-condition task without inventing a new evidence request.',
   scientific_result='The synthetic observations leave target suitability unresolved; retaining this bounded direction is the explicit fixture judgment.')
  payload.pop('decision_request',None)
 else:
  assert step==6 and 'supervision_request' not in task,task
  payload.update(next_action='finish',
   route_reason='The explicit W decision question has a bounded final answer.',
   scientific_result='Finalize this mechanical fixture from retained originals; scientific target suitability remains unqualified.',
   limitations=['No model quality, scientific qualification, extra review budget or execution permission is asserted.'])
'''.replace('QUESTION',repr(QUESTION))
    marker="if not (config['fault']=='completed-missing-output' and role=='W' and not resume):"
    monkeypatch.setattr(transport,'NATIVE',transport.NATIVE.replace(marker,
        visibility+worker+assessment+'\n'+marker,1))
    root,_,env=transport.prepare_cli(tmp_path,fault='completed-blocked',max_tokens=10000,max_seconds=240)
    try:
        result=transport.cli(root,'run',env=env,timeout=60)
    except subprocess.TimeoutExpired:
        print(json.dumps(dict(status='continuation_cli_timeout',progress=continuation_progress(root)),ensure_ascii=False))
        raise
    assert result['status']=='finish',dict(result=result,progress=continuation_progress(root))
    jobs=[c.read(root/'jobs'/f'{step:03d}.json') for step in range(1,7)]
    assert [job['role'] for job in jobs]==['H','W','W','A','W','A']
    assert jobs[3]['supervision_request']['automatic'] is True
    assert all(job.get('supervision_request') is None for job in jobs[4:])
    assert all(job.get('author_correction') is None for job in jobs)
    assessment_payload=c.read(execution.record(root,4)/'science-output.json')
    assert assessment_payload['supervision_result']=='continue_existing'
    assert assessment_payload['next_action']=='continue_w' and assessment_payload.get('decision_request') is None
    assert assessment_payload.get('search_review') is None
    assert c.read(execution.record(root,5)/'science-output.json')['stage_intent']=='decision'
    assert c.read(execution.record(root,6)/'science-output.json')['next_action']=='finish'
    expected={2:(0,0,0),3:(2,0,0),4:(4,0,0),5:(4,4,1),6:(6,4,1)}
    for step,counts in expected.items():
        observed=c.read(c.workspace(root,step)/'work/direction-visible-counters.json')
        assert (observed['total_rounds'],observed['covered_rounds'],observed['automatic_reviews'])==counts
    state=accepted_budget.read(root)['search']['supervision']
    assert (state['total_rounds'],state['covered_rounds'],state['automatic_reviews'])==(6,4,1)
    assert not (root/'jobs/007.json').exists()
    assert all(len(execution.records(root,step))==1 for step in range(2,7))
