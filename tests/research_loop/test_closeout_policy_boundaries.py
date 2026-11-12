"""Frozen capabilities and selection policy apply at the actual controller entry."""
import json
import re

import pytest

from scidiscovery.research_loop import budget, candidate_audit, contracts as c, controller, delivery
from test_candidate_audit import _seal_existing
from test_closeout_protocol import command, scene


@pytest.fixture(autouse=True)
def _stopped_source_seals(request, monkeypatch):
    if request.node.get_closest_marker('process_e2e') is None:
        from test_direct_input_bindings import stopped_seals
        stopped_seals(monkeypatch)


def _policy_scene(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain existing evidence and the exact scientific stop choice.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'], max_tokens=1000,
        max_seconds=60, max_steps=6, model='fixture', reasoning_effort='high', search_policy=None)
    return root


def test_h_wait_without_pending_enters_one_final(tmp_path, monkeypatch):
    from test_history_handoff import finish
    root = _policy_scene(tmp_path)
    _, _, record = finish(root, 1, 'H', [], action='wait')
    original = (record / 'science-output.json').read_bytes()
    calls = []
    monkeypatch.setattr(controller, '_run_final', lambda root, begin: calls.append(begin) or dict(status='final_boundary'))
    result = controller.run(root)
    begin = budget.closeout_state(root)['begin']
    assert result['status'] == 'final_boundary' and calls == [begin]
    assert begin['mode'] == 'final' and begin['stop_reason'] == 'scientific_wait'
    assert begin['source_cutoff'] == 1 and begin['first_step'] == 2 and begin.get('resume_cursor') is None
    assert controller.run(root)['status'] == 'final_boundary' and calls == [begin, begin]
    assert (record / 'science-output.json').read_bytes() == original
    assert not (root / 'jobs/002.json').exists() and not budget.closeout_state(root)['actions']


def test_h_wait_with_pending_preserves_wait_without_final(tmp_path, monkeypatch):
    from test_history_handoff import finish
    root = _policy_scene(tmp_path)
    finish(root, 1, 'H', [], action='wait')
    monkeypatch.setattr(budget, 'execution_blocked', lambda root, **kwargs: True)
    monkeypatch.setattr(controller, '_run_final', lambda *args: pytest.fail('Pending original cannot grant FINAL'))
    assert controller.run(root)['status'] == 'wait'
    assert budget.closeout_state(root)['begin'] is None and not (root / 'jobs/002.json').exists()


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
