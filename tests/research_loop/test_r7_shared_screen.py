"""Comparative observations retain one execution identity and authored meaning.

No model, solver or external call runs here. Qualified observation production is
a trusted fixture dependency; capture, reference resolution, admission and the
accepted scientific reducer execute their production implementations.
"""
import copy
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import (accepted_budget, accepted_search,
    contracts as c, history, originals, search, worker_delivery)
from scidiscovery.research_loop.output_repair import OutputRepairRequired, WorkRepairRequired


REFS=('h001.m1','h001.m2','h001.m3','h004.m4')


def test_old_opportunity_summary_is_rejected_without_replay_or_conversion(tmp_path):
    from test_direct_input_bindings import direct_instance

    root, _ = direct_instance(tmp_path)
    base = c.settings(root)
    path = root / accepted_budget.HEAD
    head = c.read(path)
    assert head['search']['version'] == accepted_search.VERSION == 2
    head['search']['version'] = 1
    c.write(path, head)
    original = path.read_bytes()
    with pytest.raises(ValueError, match='Unsupported accepted scientific summary version'):
        accepted_budget.load(root, base=base)
    assert path.read_bytes() == original


def mechanisms():
    return [dict(mechanism_ref=ref,label='Synthetic '+ref,source_step=4 if index==4 else 1,source_role='H',
        source_ref=dict(path=f'/fixture/native-evidence/{4 if index==4 else 1:03d}/result-identity.json'),
        minimal_test='Synthetic matched comparison; no scientific qualification.',status='pending',ordinal=index,
        origin_ref=dict(goal_id='fixture',source_step=4 if index==4 else 1,source_role='H',mechanism_ref=ref),source_goal_id='fixture')
        for index,ref in enumerate(REFS,1)]


def authored(call_id):
    return dict(stage_intent='continue',mechanism_findings=[dict(mechanism=ref,basis='observed',
        finding='Authored observation with uncertainty; A must judge adequacy.',call_id=call_id) for ref in REFS])


@pytest.fixture
def comparison(tmp_path,monkeypatch):
    root=tmp_path/'instance';record=root/'native-evidence/005';record.mkdir(parents=True)
    job=dict(step=5,role='W',search=dict(focus=dict(action='screen',mechanism_ref=REFS[0]),
        coverage_obligation=[{key:row[key] for key in ('mechanism_ref','source_step','source_ref','source_role')}
            for row in mechanisms()],breadth=dict(mechanisms=mechanisms(),prior_unknown=False)))
    science=authored('0001-comparison')
    c.write(record/'science-output.json',science)
    task_ref=dict(path=str(root/'jobs/005.json'))
    inventory=SimpleNamespace(attempt=0,task_ref=task_ref,caller_correction=None,file_correction=None,record=record,job=job)
    calls=[]
    def terminal(call_id,*,invalid=False):
        base=root/'host-runtime/experiment-control/005/records/calls'/call_id
        base.mkdir(parents=True)
        binding={key:mechanisms()[-1][key] for key in ('mechanism_ref','source_step','source_ref','source_role')}
        contents=dict(accepted=dict(native_attempt=dict(number=0),mechanism_binding=binding),
            completed=dict(status='known_success',result=dict(status='invalid',diagnostic='result_json_invalid') if invalid else dict(status='valid')),
            terminal=dict(status='known_success'),observation=dict(status='known_success'))
        proofs={}
        for name,value in contents.items():
            path=base/('execution-observation.json' if name=='observation' else name+'.json')
            c.write(path,value)
            relative=path.relative_to(root).as_posix()
            proofs[name]=dict(path=relative,snapshot=relative,size_bytes=path.stat().st_size,retention='archived')
        output=base/'comparison.csv';output.write_text('mechanism,value\nshared,1\n')
        relative=output.relative_to(root).as_posix()
        row=dict(call_id=call_id,status='known_success',mechanism_binding=binding,**proofs,
            originals=[],outputs=[dict(path=relative,snapshot=relative,size_bytes=output.stat().st_size,retention='archived')])
        calls.append(row)
        return row
    monkeypatch.setattr(worker_delivery,'_prefix_calls',lambda *args:())
    monkeypatch.setattr(history,'managed_calls',lambda accepted:calls)
    def capture():
        inventory.delivery=worker_delivery._capture(root,job,record,inventory=inventory)
        return inventory.delivery
    return SimpleNamespace(root=root,record=record,job=job,science=science,inventory=inventory,
        calls=calls,terminal=terminal,capture=capture)


def test_original_comparison_survives_later_same_binding_and_supports_four_authored_observations(comparison):
    scene=comparison
    first=scene.terminal('0001-comparison',invalid=True)
    scene.terminal('0002-nativebatch')
    facts=scene.capture()
    assert len(facts['evidence'])==2
    assert {row['mechanism_ref'] for row in facts['evidence']}=={REFS[-1]}
    assert any(row['accepted']==first['accepted'] and row['outputs']==first['outputs'] for row in facts['evidence'])
    assert worker_delivery._validate(scene.root,scene.job,scene.record,inventory=scene.inventory)==facts
    result=worker_delivery.require_delivery(scene.root,scene.job,scene.science,scene.record,inventory=scene.inventory)
    assert result['status']=='complete' and result['missing']==[]
    assert len(scene.calls)==2 and all(row['mechanism_binding']['mechanism_ref']==REFS[-1] for row in scene.calls)


def test_a_gets_authored_cross_mechanism_eligibility_without_new_call_bindings(comparison,monkeypatch):
    scene=comparison;scene.terminal('0001-comparison',invalid=True);facts=scene.capture()
    source = dict(step=scene.job['step'], role='W', origin_identity=dict(goal_id='fixture'),
        screen_observations=accepted_search._screen_observations(scene.job, scene.science, None, facts))
    state=dict(mechanisms={row['mechanism_ref']:row for row in mechanisms()}, assessment_workers=[source])
    monkeypatch.setattr(originals,'node',lambda *args:object())
    monkeypatch.setattr(worker_delivery,'validate_original',lambda *args:facts)
    assert search._screen_refs(scene.root,scene.job,{},state,science=scene.science)==set(REFS)
    assert all(search._mechanism_sources(scene.root,dict(role='A'),ref,state,screen=True)==[source] for ref in REFS)
    assert {row['status'] for row in state['mechanisms'].values()}=={'pending'}
    assert len(scene.calls)==1 and scene.calls[0]['mechanism_binding']['mechanism_ref']==REFS[-1]
    blocked=copy.deepcopy(scene.science);blocked['mechanism_findings'][0]['basis']='blocked'
    assert search._screen_refs(scene.root,scene.job,{},state,science=blocked)==set(REFS[1:])
    state['mechanisms'][REFS[-1]]=dict(state['mechanisms'][REFS[-1]],source_step=7)
    assert search._screen_refs(scene.root,scene.job,{},state,science=scene.science)==set()


@pytest.mark.parametrize('reference',['absent','missing','ambiguous_origin'])
def test_reference_question_after_real_work_is_output_only(comparison,reference):
    scene=comparison;scene.terminal('0001-comparison');facts=scene.capture()
    science=copy.deepcopy(scene.science)
    if reference=='absent':
        for row in science['mechanism_findings']:row.pop('call_id')
    elif reference=='missing':
        for row in science['mechanism_findings']:row['call_id']='0000-not-retained'
    else:
        extra=copy.deepcopy(facts['evidence'][0]);extra['accepted']['origin_identity']=dict(goal_id='other-origin')
        facts['evidence'].append(extra)
    with pytest.raises(OutputRepairRequired) as caught:
        worker_delivery.require_delivery(scene.root,scene.job,science,scene.record,inventory=scene.inventory)
    assert not isinstance(caught.value,WorkRepairRequired)
    assert caught.value.code=='worker_delivery_reference'
    assert 'No new execution' in str(caught.value)
    assert len(scene.calls)==1


def test_true_no_work_still_needs_continuation_and_unknown_original_is_not_replayed(comparison):
    scene=comparison;facts=scene.capture()
    with pytest.raises(WorkRepairRequired):
        worker_delivery.require_delivery(scene.root,scene.job,scene.science,scene.record,inventory=scene.inventory)
    facts['unknown']=[dict(accepted=dict(path='calls/original/accepted.json'))]
    result=worker_delivery.require_delivery(scene.root,scene.job,scene.science,scene.record,inventory=scene.inventory)
    assert result['status']=='partial' and result['reason']=='original_execution_unknown'
    assert scene.calls==[]


def test_terminal_caller_correction_remains_bounded_even_with_comparative_observations(comparison):
    scene=comparison;scene.terminal('0001-comparison');scene.capture()
    scene.inventory.caller_correction=dict(mechanism_ref=REFS[0],diagnostic='Use an available mechanism number.')
    with pytest.raises(WorkRepairRequired,match='Correct the unaccepted request'):
        worker_delivery.require_delivery(scene.root,scene.job,scene.science,scene.record,inventory=scene.inventory)
    assert len(scene.calls)==1
