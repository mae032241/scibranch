"""Soft scientific targets retain native work; finite author guards remain independently enforced."""
import json
from pathlib import Path
import sys
import time

import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, execution, native, protocol, search
from test_history_handoff import finish
from test_worker_resume import wait_runner

FAKE = r'''import json,os,pathlib,sys,time
args=sys.argv[1:];prompt=sys.stdin.read();work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);home=pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME']);role=os.environ['THIN_HWA_ROLE']
config=json.loads(pathlib.Path(sys.argv[0]).with_suffix('.json').read_text())
(work/'work/prompt.txt').write_text(prompt)
thread='11111111-2222-4333-8444-555555555555'
print(json.dumps({'type':'thread.started','thread_id':thread}),flush=True)
if config['resume']:
 p=home/'sessions/rollout.jsonl';p.parent.mkdir(parents=True,exist_ok=True)
 if not p.exists():p.write_text(json.dumps({'type':'session_meta','payload':{'id':thread,'cwd':str(work)}})+'\n')
with (work/'work/launches').open('a') as f:f.write('resume\n' if 'resume' in args else 'exec\n')
if config['mode']!='no_usage':print(json.dumps({'type':'turn.completed','usage':{'input_tokens':12,'cached_input_tokens':5,'output_tokens':3}}),flush=True)
if config['mode']=='forge_hint':
 (work/'work/budget-status.json').unlink(missing_ok=True)
 (work/'work/budget-status.json').write_text(json.dumps({'shared_observed_tokens_remaining_upper_bound':1000000000}))
if config['mode']=='known_failure' and 'resume' not in args:sys.exit(7)
time.sleep(config['sleep'])
payload=dict(summary='Original bounded finding',scientific_result='Soft target did not destroy the accepted work',limitations=['Fake process, not scientific efficacy'],implementation_selection=None)
if role=='H':payload.update(search_plan=None,next_action='experiment' if config['mode']=='reserve' else 'wait',route_reason='Bounded contribution')
if role=='W':
 job=json.loads(pathlib.Path(os.environ['THIN_HWA_JOB_MANIFEST']).read_text())
 assigned=(job.get('search') or {}).get('coverage_obligation',[])
 payload.update(stage_intent='review',mechanism_findings=[dict(mechanism=item['mechanism_ref'],basis='blocked',
  finding='This timing fixture has no managed observations; the scientific comparison remains blocked.') for item in assigned] if assigned else None,
  candidate_nominations=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=item['mechanism_ref'],
   reason='This timing fixture produces no scientific candidate to compare.')
   for item in job['candidate_nomination_mechanisms'] if item['status']!='waived'])
   if job.get('candidate_nomination_mechanisms') else None)
if role=='A':payload.update(search_review=None,candidate_recommendation=None,progress_assessment=None,decision_request=None,portfolio_review=None,next_action='finish',route_reason='Independent conclusion')
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
'''


def scene(tmp_path, *, mode='normal', tokens=10, seconds=.05, total_tokens=1000, total_seconds=30, resume=False, delivery=None, search_policy=None, sleep=.15, node_budget_mode='soft'):
    public=tmp_path/'public';public.mkdir();objective=public/'objective';objective.write_text('Complete useful scientific work within the total authorization.')
    launcher=tmp_path/'native.py';launcher.write_text(FAKE)
    c.write(launcher.with_suffix('.json'),dict(mode=mode,resume=resume,sleep=sleep))
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable,str(launcher)],max_tokens=total_tokens,max_seconds=total_seconds,max_steps=12,
        model='fake',reasoning_effort='high',search_policy=search_policy,
        node_budget_mode=node_budget_mode,
        node_budget_policy=None if tokens is None else {role:dict(max_observed_tokens=tokens,max_seconds=seconds) for role in 'HWAE'},delivery_policy=delivery,
        resume_policy=dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn') if resume else None)
    return root


def start_clock(root, reason):
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', reason)


def test_hint_symlink_does_not_write_outside_workspace(tmp_path):
    root=scene(tmp_path);job=c.build_job(root,1,'H',[])
    start_clock(root,'test accepted hint');anchor=budget.accept_node(root,job)
    outside=tmp_path/'untouched';outside.write_text('Original outside file')
    path=c.workspace(root,1)/'work/budget-status.json';path.symlink_to(outside)
    intent=dict(task_ref=c.record_ref(root/'jobs/001.json'),budget_slice=anchor)
    head=accepted_budget.accept_native(root,1,execution.origin(root,1),intent)
    shared=accepted_budget.usage_from_facts(head,job_rows=accepted_budget.load_usage_rows(head),
        current_native=dict(record=head['current_native']['record'],meter=None,metering=None,
            outcome_present=False,interrupted=False))
    facts=dict(settings=c.settings(root),head=head,shared=shared,stamp=budget.clock_stamp(),
        task=intent['task_ref'],scope=protocol.stage_scope(root,job),
        native_attempt=dict(number=0,execution_key=intent['task_ref']),
        final_return=None,closeout_preparation_seconds=None)
    budget.write_node_hint(root,job,intent,budget.snapshot(root),dict(input_tokens=0,output_tokens=0),
        facts=facts,observed=False)
    assert outside.read_text()=='Original outside file' and path.is_symlink()
