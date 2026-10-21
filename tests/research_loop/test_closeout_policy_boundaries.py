"""Frozen capabilities and selection policy apply at the actual controller entry."""
import json
import re
import sys

import pytest

from scidiscovery.research_loop import budget, candidate_audit, contracts as c, delivery, shortlist
from test_candidate_audit import _seal_existing
from test_closeout_protocol import NATIVE, command, scene
from test_control_selection import scene as selection_scene


def test_disabled_audit_with_adapter_returns_unavailable_without_external_execution(tmp_path):
    root=scene(tmp_path,[dict(action='audit',candidate='run-1'),dict(action='wait',candidate='run-1')],audit=False)
    result=command(root,'run')
    assert result['status']=='wait',result
    receipt=c.read(candidate_audit._directory(root,3)/'outcome.json')
    assert receipt['accepted'] is False
    assert receipt['result']['status']=='unavailable' and receipt['result']['note']=='candidate_audit_disabled'
    assert not list(candidate_audit.facts(root)) and budget.used_steps(root)==4
    assert not (root/'host-delivery/leaf-output').exists() and not (root/'host-delivery/resolved').exists()
    task=c.read(c.workspace(root,3)/'science-task.json')
    assert task['audit_capability']==dict(available=False,unavailable_reason='candidate_audit_disabled')
    prompt=(c.workspace(root,3)/'work/native-prompt').read_text()
    options=json.loads(re.search(r'"next_action_options": (\[[^\]]*\])',prompt).group(1))
    assert 'audit' not in options
    assert c.read(c.workspace(root,4)/'science-task.json')['candidate_audit']['status']=='unavailable'
    assert budget.closeout_state(root)['begin'] is None and not budget.closeout_state(root)['actions']
    assert not (root/'submission/model.pkl').exists() and not (root/'runs/submitted.json').exists()
    original=(candidate_audit._directory(root,3)/'outcome.json').read_bytes()
    assert command(root,'run')['status']=='wait'
    assert (candidate_audit._directory(root,3)/'outcome.json').read_bytes()==original
    assert not list(candidate_audit.facts(root)) and budget.used_steps(root)==4


@pytest.mark.parametrize('low_budget',[False,True])
def test_accepted_audit_without_policy_still_counts_and_projects_unknown(tmp_path,low_budget):
    root=scene(tmp_path,[],audit=False,max_tokens=1120 if low_budget else 100000)
    nodes=[dict(step=n,seal_sha256=c.digest(c.host_record(c.workspace(root,n))/'result-identity.json')) for n in (1,2)]
    refs=delivery.catalogue_sources(root,nodes)
    budget.begin_closeout(root,2,3,'Retained accepted audit anomaly',delivery_sources=refs)
    job=c.build_job(root,3,'A',[1,2],purpose='closeout',source_cutoff=2,delivery_sources=refs)
    payload=dict(summary='Retained audit request',scientific_result='No qualification is asserted.',limitations=['Unknown external audit'],
        implementation_selection=None,search_review=None,progress_assessment=None,candidate_recommendation='run-1',
        next_action='audit',route_reason='Read the original accepted operation')
    _seal_existing(root,3,payload)
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'advance','Original accepted audit')
    decision=candidate_audit._decision(root,3);operation=candidate_audit._operation(root,decision,'run-1')
    path=candidate_audit._directory(root,3)/'intent.json'
    candidate_audit._save(path,dict(action_id=operation,action_kind='audit',operation_id=operation,decision=decision,
        candidate_id='run-1',max_seconds=20,advance_seconds=budget.snapshot(root)['advance_seconds']))
    before=path.read_bytes()
    assert budget.used_steps(root)==4 and budget.execution_blocked(root)
    assert command(root,'status')['closeout']['status']=='candidate_audit_unknown'
    assert command(root,'run')['status']=='candidate_audit_unknown'
    assert path.read_bytes()==before and not path.with_name('outcome.json').exists()
    assert not (root/'host-delivery/leaf-output').exists()


AUTHOR=r'''import json,os,pathlib,sys
args=sys.argv[1:];sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
root=pathlib.Path(os.environ['THIN_HWA_JOB_MANIFEST']).parents[1]
task=json.loads((work/'science-task.json').read_text())
assert not (root/'submission/model.pkl').exists()
table=task['closeout']['comparison']
assert 'default' not in table and len(table['rows'])==3
chosen='run-3' if ALTERNATIVE else 'run-2'
comparison=dict(rows=[dict(candidate_id=r['candidate_id'],key_risk='Fixture public evidence only',decision='keep' if r['candidate_id']==chosen else 'replace') for r in table['rows']],
 rationale='Original W observation supports the explicit choice',evidence_steps=[2])
payload=dict(summary='Compare frozen policy screen',scientific_result='Bounded public choice; target performance remains unknown.',limitations=['Fixture only'],
 implementation_selection=None,search_review=None,progress_assessment=None,decision_request=None,portfolio_review=None,
 next_action='deliver',route_reason='Explicit choice after policy screening',candidate_recommendation=chosen,candidate_comparison=comparison)
if isinstance(payload.get('candidate_recommendation'),str):payload['selection_explanation']=dict(criteria='Compare the declared public fixture evidence and retain its stated limitations.',criteria_change=dict(status='unchanged',prior_criteria=None,changes=None,evidence=None,accepted_tradeoffs=None))
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))),flush=True)
'''


@pytest.mark.parametrize('alternative',[False,True])
def test_ordinary_deliver_with_selection_policy_cannot_bypass_frozen_comparison(tmp_path,alternative):
    launcher=tmp_path/'author.py';launcher.write_text('ALTERNATIVE='+repr(alternative)+'\n'+AUTHOR)
    root=selection_scene(tmp_path,launcher=[sys.executable,str(launcher)],author_action='deliver')
    result=command(root,'run')
    assert result['status']=='delivery_confirmed',result
    assert result['last_confirmed']['candidate_id']==('run-3' if alternative else 'run-2')
    jobs=[c.read(p) for p in sorted((root/'jobs').glob('*.json'))]
    assert [j['role'] for j in jobs]==['H','W','A','A']
    assert jobs[2].get('purpose') is None and jobs[3]['purpose']=='closeout'
    assert jobs[3]['selection']==shortlist.marker(root) and jobs[3].get('author_correction') is None
    assert not list((root/'host-delivery/selection').rglob('*.intent.json')) and len(budget.closeout_state(root)['actions'])==2
    assert c.read(c.workspace(root,3)/'science-task.json')['delivery_selection']
    assert command(root,'run')['status']=='delivery_confirmed'
    assert len(list((root/'jobs').glob('*.json')))==4


def test_ordinary_deliver_without_selection_policy_requires_one_explicit_final_choice(tmp_path):
    launcher=tmp_path/'author.py'
    launcher.write_text("SCENARIO="+repr([dict(action='deliver',candidate='run-2')])+"\nFIRST_STEP=4\n"+NATIVE)
    root=selection_scene(tmp_path,policy=None,launcher=[sys.executable,str(launcher)],author_action='deliver')
    originals={path:path.read_bytes() for path in (root/'jobs/003.json',root/'native-evidence/003/science-output.json',
        root/'native-evidence/003/result-identity.json',c.workspace(root,2)/'artifacts/train.py')}
    assert c.read(root/'native-evidence/003/science-output.json')['candidate_recommendation']=='run-3'
    assert not budget.closeout_state(root)['actions'] and not (root/'submission/model.pkl').exists()
    result=command(root,'run')
    assert result['status']=='delivery_confirmed' and result['last_confirmed']['candidate_id']=='run-2',result
    jobs=[c.read(path) for path in sorted((root/'jobs').glob('*.json'))]
    assert [job['role'] for job in jobs]==['H','W','A','A']
    assert all(job.get('purpose') is None for job in jobs[:3]) and jobs[3]['final_only']
    assert 'selection' not in jobs[3] and shortlist.marker(root) is None
    assert 'comparison' not in c.read(c.workspace(root,4)/'science-task.json')['closeout']
    assert not list((root/'host-delivery/selection').rglob('*.intent.json'))
    actions=budget.closeout_state(root)['actions']
    assert [row['intent']['action_kind'] for row in actions]==['submit','confirm']
    assert all(row['intent']['decision']['step']==4 and row['intent']['binding']['candidate_id']=='run-2' for row in actions)
    source=actions[0]['intent']['binding']['source']
    assert source['objects']['source.py']['sha256']==c.digest(c.workspace(root,2)/'artifacts/train.py')
    assert source['objects']['artifact']['sha256']==c.digest(root/'runs/model-2.pkl')
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    assert command(root,'run')['status']=='delivery_confirmed' and budget.closeout_state(root)['actions']==actions
    assert all(path.read_bytes()==raw for path,raw in originals.items()) and not (root/'jobs/005.json').exists()
