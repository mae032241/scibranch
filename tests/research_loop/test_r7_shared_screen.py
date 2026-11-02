"""Comparative observations retain one execution identity and authored meaning.

No model, solver or external call runs here. Qualified observation production is
a trusted fixture dependency; capture, reference resolution, admission and the
accepted scientific reducer execute their production implementations.
"""
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import (accepted_budget, accepted_search, breadth,
    contracts as c, experiments, history, originals, search, worker_delivery)
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
    return [dict(mechanism_ref=ref,label='Synthetic '+ref,source_h=4 if index==4 else 1,
        source_h_ref=dict(path=f'/fixture/native-evidence/{4 if index==4 else 1:03d}/result-identity.json'),
        minimal_test='Synthetic matched comparison; no scientific qualification.',status='pending',ordinal=index,
        origin_ref=dict(goal_id='fixture',source_h=4 if index==4 else 1,mechanism_ref=ref),source_goal_id='fixture')
        for index,ref in enumerate(REFS,1)]


def authored(call_id):
    return dict(stage_intent='review',mechanism_findings=[dict(mechanism=ref,basis='observed',
        finding='Authored observation with uncertainty; A must judge adequacy.',call_id=call_id) for ref in REFS])


@pytest.fixture
def comparison(tmp_path,monkeypatch):
    root=tmp_path/'instance';record=root/'native-evidence/005';record.mkdir(parents=True)
    job=dict(step=5,role='W',search=dict(focus=dict(action='screen',mechanism_ref=REFS[0]),
        coverage_obligation=[{key:row[key] for key in ('mechanism_ref','source_h','source_h_ref')}
            for row in mechanisms()],breadth=dict(mechanisms=mechanisms(),prior_unknown=False)))
    science=authored('0001-comparison')
    c.write(record/'science-output.json',science)
    task_ref=dict(path=str(root/'jobs/005.json'))
    inventory=SimpleNamespace(attempt=0,task_ref=task_ref,caller_correction=None,file_correction=None,record=record,job=job)
    calls=[]
    def terminal(call_id,*,invalid=False):
        base=root/'host-runtime/experiment-control/005/records/calls'/call_id
        base.mkdir(parents=True)
        binding={key:mechanisms()[-1][key] for key in ('mechanism_ref','source_h','source_h_ref')}
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
    state=dict(mechanisms={row['mechanism_ref']:row for row in mechanisms()})
    monkeypatch.setattr(originals,'node',lambda *args:object())
    monkeypatch.setattr(worker_delivery,'validate_original',lambda *args:facts)
    monkeypatch.setattr(history,'direct_sources',lambda *args:[(scene.job,scene.science,{})])
    assert search._screen_refs(scene.root,scene.job,{},state,science=scene.science)==set(REFS)
    assert all(search._mechanism_sources(scene.root,dict(role='A'),ref,state,screen=True)==[scene.job] for ref in REFS)
    assert {row['status'] for row in state['mechanisms'].values()}=={'pending'}
    assert len(scene.calls)==1 and scene.calls[0]['mechanism_binding']['mechanism_ref']==REFS[-1]
    blocked=copy.deepcopy(scene.science);blocked['mechanism_findings'][0]['basis']='blocked'
    assert search._screen_refs(scene.root,scene.job,{},state,science=blocked)==set(REFS[1:])
    state['mechanisms'][REFS[-1]]=dict(state['mechanisms'][REFS[-1]],source_h=7)
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


@pytest.fixture
def admission(tmp_path,monkeypatch):
    root=tmp_path/'instance';rows=mechanisms();prefix=dict(version=1,cutoff=4)
    value=dict(version=2,initial_calls=1,mechanisms=rows,
        baseline=[dict(mechanism_ref=row['mechanism_ref'],accepted_count=1 if index<3 else 0) for index,row in enumerate(rows)],
        grant=None,prior_unknown=False,outside_initial_owed=0,catalog=dict(prefix=prefix,max_ordinal=4))
    scope=dict(step=5,job_ref=dict(path=str(root/'jobs/005.json')))
    focus=dict(mechanism_ref=REFS[0],action='screen')
    job=dict(step=5,search=dict(breadth=value,focus=focus),history=dict(prefix=prefix))
    def publish():c.write(root/'jobs/005.json',job)
    publish()
    items={row['mechanism_ref']:dict(row,accepted_calls=1 if index<3 else 0,
        source_origin_identity=dict(goal_id='fixture')) for index,row in enumerate(rows)}
    head=dict(prefix=prefix,search=dict(mechanism_count=4,owed_count=1,opportunity_unknown_count=0,
        table=dict(mechanisms=items,ordinals=list(REFS))))
    monkeypatch.setattr(accepted_budget,'read',lambda selected:head)
    return SimpleNamespace(root=root,value=value,scope=scope,head=head,items=items,focus=focus,publish=publish)


def test_public_numeric_choices_and_admission_share_the_owed_opportunity_calculation(admission):
    lane=admission
    with pytest.raises(breadth.OpportunityRequired) as caught:
        breadth.admit(lane.value,'1',lane.scope,[])
    error=caught.value
    assert error.reason=='initial_opportunities_owed'
    assert error.available_mechanisms==[dict(number='4',mechanism_ref=REFS[-1])]
    with pytest.raises(experiments._AdmissionOpportunityRequired) as public:
        experiments._raise_admission_refusal(error)
    assert public.value.reason==error.reason and public.value.available_mechanisms==error.available_mechanisms
    binding=breadth.admit(lane.value,error.available_mechanisms[0]['number'],lane.scope,[])
    assert binding['mechanism_ref']==REFS[-1] and binding['catalog_binding']['ordinal']==4 and binding['grant'] is None
    assert breadth.admit(lane.value,'1',lane.scope,[dict(mechanism_binding=binding)])['mechanism_ref']==REFS[0]


def test_bound_task_advice_uses_the_same_pure_admission_calculation(admission,monkeypatch):
    lane=admission
    state=dict(summary=lane.head['search'],current_h=4,mechanisms=lane.items,focus=lane.focus,
        authorized=set(REFS),opportunity_counts={ref:item['accepted_calls'] for ref,item in lane.items.items()},
        opportunity_grants={},opportunity_unknown=False,extra_grant=None,_catalog=lane.value['catalog'],
        order=dict(allocation=None,remaining=[],diagnostics=[]))
    monkeypatch.setattr(search.configuration_updates,'for_new_job',
        lambda *args:dict(search_policy=dict(search.DEFAULT_POLICY,initial_calls=1)))
    def forbidden(*args,**kwargs):pytest.fail('Produced task advice must not read originals or filesystem metadata')
    with monkeypatch.context() as no_io:
        no_io.setattr(c,'read',forbidden)
        no_io.setattr(accepted_budget,'read',forbidden)
        no_io.setattr(Path,'read_text',forbidden)
        no_io.setattr(Path,'read_bytes',forbidden)
        no_io.setattr(Path,'stat',forbidden)
        bound=search.bind(lane.root,5,'W',state=state)
        projected=c._role_lineage(dict(role='W',search=bound,
            history=dict(view=dict(node_count=4,recent=[],mechanisms={},candidates={}))))
    assert bound['focus']==lane.focus
    with pytest.raises(breadth.OpportunityRequired) as refusal:breadth.admit(lane.value,'1',lane.scope,[])
    assert bound['admission_advice']==dict(available_mechanisms=refusal.value.available_mechanisms,
        omitted_count=0,focus_reason=refusal.value.reason)
    assert bound['admission_advice']['available_mechanisms']==[dict(number='4',mechanism_ref=REFS[-1])]
    assert projected['mechanisms']['admission']==bound['admission_advice']
    numbers={row['mechanism_ref']:row['number'] for row in projected['mechanisms']['overview']}
    assert numbers[REFS[-1]]=='4' and numbers[REFS[0]]=='1'


def test_invalid_choice_unknown_original_and_exact_extra_grant_keep_numeric_advice_honest(admission):
    lane=admission
    with pytest.raises(breadth.OpportunityRequired) as caught:breadth.admit(lane.value,'unknown',lane.scope,[])
    assert caught.value.reason=='mechanism_choice_required'
    assert caught.value.available_mechanisms==[dict(number='4',mechanism_ref=REFS[-1])]
    lane.value['grant']=dict(mechanism_ref=REFS[0],source_a=3,source_a_ref=dict(path='/fixture/a3.json'),extra_calls=1,used_calls=0)
    lane.publish()
    first=breadth.admit(lane.value,'1',lane.scope,[])
    assert first['grant']==dict(source_a=3,source_a_ref=dict(path='/fixture/a3.json'))
    with pytest.raises(breadth.OpportunityRequired) as spent:breadth.admit(lane.value,'1',lane.scope,[dict(mechanism_binding=first)])
    assert spent.value.reason=='initial_opportunities_owed'
    assert spent.value.available_mechanisms==[dict(number='4',mechanism_ref=REFS[-1])]
    lane.head['search']['opportunity_unknown_count']=1
    with pytest.raises(breadth.OpportunityRequired) as unknown:breadth.admit(lane.value,'4',lane.scope,[])
    assert unknown.value.reason=='original_execution_unknown' and unknown.value.available_mechanisms==[]


def test_scientific_screening_releases_opportunity_without_fabricating_calls_or_investment(tmp_path):
    summary=accepted_search.empty();facts={};origin=dict(goal_id='fixture');policy=dict(search_policy=dict(initial_calls=1))
    observation=dict(coverage=dict(status='complete'),managed_calls=[],candidates=[],contexts=[],current_candidate=None)
    control=dict(search_status='applied',selection_status='applied',decision_status='applied',resolved_focus=None,resolved_allocation=None)
    def apply(job,payload,observed=None):
        nonlocal summary
        result=accepted_search.prepare_node_from_facts(tmp_path,job,payload,dict(control_outcome=control,
            observations=dict(path=f'observation-{job["step"]}.json')),summary=summary,lookup=facts.get,
            observation=observation if observed is None else observed,policy=policy,origin=origin)
        summary=result['summary'];facts.update(result['rows'])
    apply(dict(step=1,role='H',direct_refs=[]),dict(search_plan=dict(commitments=[dict(label=ref,minimal_test='Synthetic comparison') for ref in REFS])))
    refs=list(summary['table']['mechanisms']);binding=dict(mechanism_ref=refs[-1],source_h=1,
        source_h_ref=dict(path=str(tmp_path/'native-evidence/001/result-identity.json')),grant=None)
    apply(dict(step=2,role='W',direct_refs=[],search=dict(focus=dict(mechanism_ref=refs[0],action='screen'),breadth=True,allocation=None)),
        dict(stage_intent='review'),dict(observation,managed_calls=[dict(call_id='one-comparative-call',status='known_success',mechanism_binding=binding)]))
    assert summary['owed_count']==3
    counts={ref:item['accepted_calls'] for ref,item in summary['table']['mechanisms'].items()}
    assert counts=={ref:int(ref==refs[-1]) for ref in refs}
    apply(dict(step=3,role='A',direct_refs=[dict(path='inputs/parents/002/conclusion.json')]),
        dict(next_action='finish',search_review=dict(dispositions=[dict(mechanism_ref=ref,status='screened',reason='Independent authored comparison judgment') for ref in refs])))
    assert summary['owed_count']==0 and summary['extra_grant'] is None
    assert {ref:item['accepted_calls'] for ref,item in summary['table']['mechanisms'].items()}==counts
    assert {ref:item['participated_cycles'] for ref,item in summary['table']['mechanisms'].items()}==counts
    apply(dict(step=4,role='A',direct_refs=[]),dict(next_action='finish',search_review=dict(dispositions=[dict(mechanism_ref=refs[0],status='pending',reason='A changed scientific question reopens this mechanism')])) )
    assert summary['owed_count']==1
    assert {ref:item['accepted_calls'] for ref,item in summary['table']['mechanisms'].items()}==counts
