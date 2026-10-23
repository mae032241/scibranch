"""Frozen capabilities and selection policy apply at the actual controller entry."""
import json
from pathlib import Path
import re
import sys

import pytest

from scidiscovery.research_loop import budget, candidate_audit, contracts as c, controller, delivery
from test_candidate_audit import _seal_existing
from test_closeout_protocol import command, scene
from test_control_selection import scene as selection_scene
from test_delivery import failure_diagnostics


@pytest.mark.process_e2e
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


@pytest.mark.process_e2e
@pytest.mark.parametrize('low_budget',[False,True])
def test_accepted_audit_without_policy_still_counts_and_projects_unknown(tmp_path,low_budget):
    root=scene(tmp_path,[],audit=False,max_tokens=1120 if low_budget else 100000)
    nodes=[dict(step=n,seal_ref=c.record_ref(c.host_record(c.workspace(root,n))/'result-identity.json')) for n in (1,2)]
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


@pytest.mark.process_e2e
@pytest.mark.parametrize('policy',[None,{'version':2}])
def test_ordinary_deliver_retains_exact_author_and_choice_without_final(tmp_path,policy):
    launcher=tmp_path/'author.py';launcher.write_text("raise AssertionError('No new scientific author is authorized')\n")
    root=selection_scene(tmp_path,policy=policy,launcher=[sys.executable,str(launcher)],author_action='deliver')
    originals={path:path.read_bytes() for path in (root/'jobs/003.json',root/'native-evidence/003/science-output.json',
        root/'native-evidence/003/result-identity.json',c.workspace(root,2)/'artifacts/train.py')}
    assert c.read(root/'native-evidence/003/science-output.json')['candidate_recommendation']=='run-3'
    if policy:
        job=c.read(root/'jobs/003.json');task=c.read(c.workspace(root,3)/'science-task.json')
        assert job['selection']['normal_step']==3
        assert {row['candidate_id'] for row in task['delivery_comparison']['rows']}=={'run-1','run-2','run-3'}
    result=command(root,'run')
    assert result['status']=='delivery_confirmed' and result['last_confirmed']['candidate_id']=='run-3',(result,failure_diagnostics(root))
    jobs=[c.read(path) for path in sorted((root/'jobs').glob('*.json'))]
    assert [job['role'] for job in jobs]==['H','W','A']
    assert all(job.get('purpose') is None for job in jobs) and budget.final_a_barrier(root) is None
    assert budget.closeout_state(root)['begin']['authorization_step']==3
    actions=budget.closeout_state(root)['actions']
    assert [row['intent']['action_kind'] for row in actions]==['submit','confirm']
    assert all(row['intent']['decision']['step']==3 and row['intent']['binding']['candidate_id']=='run-3' for row in actions)
    source=actions[0]['intent']['binding']['source']
    assert Path(source['objects']['source.py']['path']).read_bytes()==(c.workspace(root,2)/'artifacts/train.py').read_bytes()
    assert Path(source['objects']['artifact']['path']).read_bytes()==(root/'runs/model-3.pkl').read_bytes()
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    assert command(root,'run')['status']=='delivery_confirmed' and budget.closeout_state(root)['actions']==actions
    assert all(path.read_bytes()==raw for path,raw in originals.items()) and not (root/'jobs/004.json').exists()


@pytest.mark.process_e2e
def test_ordinary_finish_declines_delivery_without_final_or_submit(tmp_path):
    launcher=tmp_path/'author.py';launcher.write_text("raise AssertionError('No new scientific author is authorized')\n")
    root=selection_scene(tmp_path,launcher=[sys.executable,str(launcher)],author_action='finish')
    original=(root/'native-evidence/003/science-output.json').read_bytes()
    assert command(root,'run')['status']=='finish'
    assert budget.closeout_state(root)['begin'] is None and not budget.closeout_state(root)['actions']
    assert budget.final_a_barrier(root) is None and not (root/'jobs/004.json').exists()
    assert not (root/'submission/model.pkl').exists()
    assert command(root,'run')['status']=='finish'
    assert (root/'native-evidence/003/science-output.json').read_bytes()==original


@pytest.mark.process_e2e
def test_ordinary_delivery_preparation_failure_retains_author_without_new_final(tmp_path,monkeypatch):
    root=selection_scene(tmp_path,author_action='deliver')
    monkeypatch.setattr(delivery,'prepare_binding',lambda *args:(_ for _ in ()).throw(delivery.DeliveryUnresolved('missing_exact_qualification')))
    original=(root/'native-evidence/003/science-output.json').read_bytes()
    result=controller.run(root)
    assert result['status']=='closeout_unresolved' and result['reason']=='missing_exact_qualification'
    assert not budget.closeout_state(root)['actions'] and budget.final_a_barrier(root) is None
    assert not (root/'jobs/004.json').exists() and not (root/'submission/model.pkl').exists()
    assert controller.run(root)['status']=='closeout_unresolved'
    assert (root/'native-evidence/003/science-output.json').read_bytes()==original
