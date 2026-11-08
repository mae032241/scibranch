"""Frozen v2 readiness/refusal contracts; fixtures never invoke a model or solver."""
import copy

import pytest

from scidiscovery.research_loop import (
    accepted_budget, budget, contracts as c, controller, delivery, delivery_review, handoff_state,
    native, role_share, supervision,
)
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_decision_contract import REQUEST
from test_direction_supervision_routing import facts
from test_history_handoff import finish
from test_worker_led_strategy import instance, publish, chain


def v2(tmp_path):
    return instance(tmp_path,strategy=2)


def review(root,step=3,source=2,used=0,parents=None):
    return c.build_job(root,step,'A',parents or [1,source],
        evidence_return=delivery_review.make('delivery_review',source,used))


def test_prepared_final_declines_with_candidate_inventory_and_seals(tmp_path,monkeypatch):
    root=v2(tmp_path);chain(root)
    budget.begin_closeout(root,2,3,'Fixture exhausted FINAL',final_only=True)
    job=c.build_job(root,3,'A',[1,2],final_report=dict(source_cutoff=2,stopped_original=None))
    # The response contract depends only on the frozen candidate inventory;
    # availability never changes a v2 final assessor's right to reject all.
    with_candidates=dict(job,delivery_sources=dict(items=[dict(candidate_id='retained-invalid')]))
    assert c.result_schema('A',**c.response_options(with_candidates))['properties']['next_action']['enum']==['deliver','finish']
    payload=publish(root,job,action='finish',candidate=None,
        science='Every candidate fails the original validation object; no candidate is accepted.')
    monkeypatch.setattr(delivery,'prepare_binding',lambda *args: pytest.fail('Refusal must not bind delivery'))
    monkeypatch.setattr(delivery,'invoke',lambda *args,**kwargs: pytest.fail('Refusal has no adapter effect'))
    result=controller._final_a_result(root,job,payload)
    assert result['reason']=='scientific_delivery_declined'
    assert result['submission_result']['status']=='unsubmitted'
    assert not budget.closeout_state(root)['actions']


def test_one_gap_requires_request_and_final_reassessment_has_no_second_correction(tmp_path):
    invalid=v2(tmp_path/'missing-request');chain(invalid)
    with pytest.raises(OutputRepairRequired,match='decision_request'):
        publish(invalid,review(invalid),action='continue_w')
    root=v2(tmp_path/'one-correction');chain(root)
    first=review(root)
    publish(root,first,action='continue_w',request=copy.deepcopy(REQUEST))
    route=controller._route(root,3,'A',handoff_state.read_sealed(root,3),first,1,2)
    assert route[0]=='W' and route[3]==delivery_review.make('delivery_correction',3,1)
    correction=c.build_job(root,4,'W',route[1],instruction=route[2],evidence_return=route[3])
    task=c.read(c.workspace(root,4)/'science-task.json')
    assert REQUEST['question'] in str(task) and REQUEST['change_criterion'] in str(task)
    schema=c.result_schema('W',**c.response_options(correction))
    assert schema['properties']['stage_intent']['enum']==['continue','handoff','decision']
    assert schema['properties']['search_plan']==dict(type='null')
    finish(root,4,'W',route[1],text='Corrected the exact validation-object gap.')
    route=controller._route(root,4,'W',handoff_state.read_sealed(root,4),correction,1,2)
    second=c.build_job(root,5,'A',route[1],instruction=route[2],evidence_return=route[3])
    assert delivery_review.binding(second)['corrections_used']==1
    assert 'continue_w' not in c.result_schema('A',**c.response_options(second))['properties']['next_action']['enum']
    with pytest.raises(OutputRepairRequired):
        publish(root,second,action='continue_w',request=copy.deepcopy(REQUEST))


def test_handoff_cold_route_and_parent_binding_cannot_reset_correction(tmp_path):
    root=v2(tmp_path);chain(root)
    first=review(root);publish(root,first,request=copy.deepcopy(REQUEST))
    binding=delivery_review.make('delivery_correction',3,1)
    job=c.build_job(root,4,'W',[1,2,3],evidence_return=binding)
    finish(root,4,'W',[1,2,3],stage_intent='handoff')
    route=controller._route(root,4,'W',handoff_state.read_sealed(root,4),job,1,2)
    controller._save_route(root,4,(*route[:4],None,None),route[4],route[5],resumed=None)
    saved=controller._saved_route(root,first_step=1,resumed=None)
    assert saved['route']['evidence_return']==delivery_review.make('delivery_correction',4,1)
    next_job=c.build_job(root,5,'W',route[1],evidence_return=route[3],instruction=route[2])
    assert delivery_review.binding(next_job)['corrections_used']==1
    with pytest.raises(ValueError,match='allowance|scope'):
        c.build_job(root,6,'A',[1,4],evidence_return=delivery_review.make('delivery_review',4,0))
    with pytest.raises(ValueError,match='source'):
        c.build_job(root,7,'A',[1],evidence_return=delivery_review.make('delivery_review',4,1))


def test_ordinary_assessment_cannot_authorize_but_requests_scoped_final_review(tmp_path):
    root=v2(tmp_path);chain(root)
    job=c.build_job(root,3,'A',[1,2]);payload=publish(root,job,action='finish')
    assert controller._worker_led_final_required(job)
    route=controller._route(root,3,'A',payload,job,1,2)
    final=c.build_job(root,4,'A',route[1],evidence_return=route[3],instruction=route[2])
    assert delivery_review.scope(final)=='delivery_review'
    assert not controller._worker_led_final_required(final)
    admitted=role_share.effective_route(root,3,payload,job,(*route,None,None))
    assert admitted[-1] is None and admitted[0]=='A'


def test_due_supervision_does_not_replace_readiness_or_correction(tmp_path):
    root=v2(tmp_path);chain(root)
    worker=c.read_job(root/'jobs/002.json')
    authored=handoff_state.read_sealed(root,2)
    route=controller._route(root,2,'W',authored,worker,1,None)
    selected,request=controller._supervision_route(2,authored,(*route,None,None),facts=facts(),source_job=worker)
    assert selected==(*route,None,None) and request is None
    job=review(root)
    assert supervision.worker_review(dict(job,role='W',evidence_return=delivery_review.make('delivery_correction',2,1)),{},
        summary={},budget_fraction=.8,selected_policy=supervision.DEFAULT_POLICY) is None


def test_exhausted_original_budget_never_reopens_correction_w(tmp_path,monkeypatch):
    root=v2(tmp_path);chain(root)
    first=review(root);publish(root,first,request=copy.deepcopy(REQUEST))
    stamp=budget.clock_stamp()
    later=dict(stamp,at_unix=stamp['at_unix']+10000,at_boottime=stamp['at_boottime']+10000)
    c.write(root/'host-started.json',stamp)
    budget.transition(root,'advance','Fixture consumes original active time')
    monkeypatch.setattr(budget,'clock_stamp',lambda:dict(later))
    assert budget.snapshot(root)['seconds_remaining']==0
    calls=[]
    def final_call(root,step,**kwargs):
        job=c.read_job(root/'jobs'/f'{step:03d}.json')
        assert job['role']=='A' and job['final_only']
        calls.append(step);publish(root,job,action='finish')
    monkeypatch.setattr(controller,'_native_call',final_call)
    result=controller.run(root)
    assert result['status']=='final_a_completed',result
    assert calls and c.read(root/'jobs/004.json')['role']=='A'
    assert not (root/'jobs/005.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('entry',['voluntary','protected'])
def test_real_candidate_final_rejection_has_no_adapter_binding_or_effect(tmp_path,monkeypatch,entry):
    from test_closeout_protocol import scene
    root=scene(tmp_path,[])
    called=[]
    def final_call(root,step,**kwargs):
        job=c.read_job(root/'jobs'/f'{step:03d}.json')
        if entry=='protected':
            assert job['final_only'] and job['delivery_sources']['items']
        else:
            assert delivery_review.scope(job)=='delivery_review' and not job.get('final_only')
        assert 'finish' in c.result_schema('A',**c.response_options(job))['properties']['next_action']['enum']
        called.append(step);publish(root,job,action='finish',candidate=None,
            science='Retained candidates use the wrong evaluation object; none is scientifically accepted.')
    monkeypatch.setattr(controller,'_native_call',final_call)
    monkeypatch.setattr(delivery,'prepare_binding',lambda *args: pytest.fail('Rejected candidates cannot bind'))
    monkeypatch.setattr(delivery,'invoke',lambda *args,**kwargs: pytest.fail('Rejected candidates cannot submit'))
    result=(controller.closeout if entry=='protected' else controller.run)(root)
    assert result['reason']=='scientific_delivery_declined' and called==[3]
    assert not budget.closeout_state(root)['actions']
    assert not (root/'submission/model.pkl').exists()
    assert not (root/'host-delivery/binding.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('uncertain',[False,True])
def test_voluntary_ready_one_final_a_delivers_exact_candidate_and_reconciles_original(tmp_path,monkeypatch,uncertain):
    from test_closeout_protocol import scene
    root=scene(tmp_path,[])
    calls=[];effects=[];invoke=delivery.invoke
    def final_call(root,step,**kwargs):
        job=c.read_job(root/'jobs'/f'{step:03d}.json')
        assert step==3 and delivery_review.scope(job)=='delivery_review' and not job.get('final_only')
        calls.append(step);publish(root,job,action='deliver',candidate='run-1')
    def maybe_unknown(root,intent,remaining,**kwargs):
        effects.append((intent['action_kind'],intent['operation_id']))
        result=invoke(root,intent,remaining,**kwargs)
        if uncertain and intent['action_kind']=='submit':
            assert result['status']=='confirmed'
            return delivery._result(intent['binding'],'unknown','Fixture lost original receipt')
        return result
    monkeypatch.setattr(controller,'_native_call',final_call)
    monkeypatch.setattr(delivery,'invoke',maybe_unknown)
    result=controller.run(root)
    if uncertain:
        assert result['status']=='delivery_unknown'
        result=controller.run(root)
    assert result['status']=='delivery_confirmed',result
    assert calls==[3] and not (root/'jobs/004.json').exists()
    actions=budget.closeout_state(root)['actions']
    assert [row['intent']['action_kind'] for row in actions]==['submit','confirm']
    assert all(row['intent']['decision']['step']==3 and row['intent']['binding']['candidate_id']=='run-1' for row in actions)
    assert len({operation for _,operation in effects})==1
    assert [kind for kind,_ in effects]==['submit','confirm']
    assert budget.final_a_barrier(root) is None
    assert controller.run(root)['status']=='delivery_confirmed' and calls==[3]


@pytest.mark.parametrize('changed',[dict(step=1,corrections_used=0),dict(step=2,corrections_used=1)])
def test_input_owner_rejects_changed_scope_before_sealing(tmp_path,changed):
    root=v2(tmp_path);chain(root);job=review(root)
    changed_job=dict(job,evidence_return=dict(job['evidence_return'],**changed))
    base=c.settings(root);head=accepted_budget.read(root)
    with pytest.raises(ValueError,match='scope|allowance'):
        c.load_validated_input_facts(root,c.workspace(root,3),changed_job,
            base=base,policy=base,head=head)



def test_controller_runs_one_bounded_correction_and_one_reassessment(tmp_path,monkeypatch):
    root=v2(tmp_path);chain(root);calls=[]
    def execute(root,step,**kwargs):
        job=c.read_job(root/'jobs'/f'{step:03d}.json');calls.append((step,job['role']))
        if step==3:
            assert delivery_review.binding(job)['corrections_used']==0
            publish(root,job,request=copy.deepcopy(REQUEST))
        elif step==4:
            assert delivery_review.scope(job)=='delivery_correction'
            assert job['scientific_work_unit']==c.guidance.WORK_UNITS['validate']
            finish(root,step,'W',[int(ref['path'].split('/')[2]) for ref in job['direct_refs']],
                text='The bounded observation corrects the decision-changing validation gap.')
        else:
            assert step==5 and delivery_review.binding(job)['corrections_used']==1
            publish(root,job,action='finish',science='The corrected evidence remains insufficient; decline with limitations.')
    monkeypatch.setattr(controller,'_native_call',execute)
    result=controller.run(root)
    assert result['status']=='finish' and result['reason']=='scientific_delivery_declined',result
    assert calls==[(3,'A'),(4,'W'),(5,'A')] and not (root/'jobs/006.json').exists()
    assert controller.run(root)['status']=='finish' and calls==[(3,'A'),(4,'W'),(5,'A')]


def test_readiness_review_required_share_rule_is_identical_on_resume(tmp_path,monkeypatch):
    job=dict(step=3,role='A',research_strategy_version=2)
    target=delivery_review.make('delivery_review',3,0)
    monkeypatch.setattr(role_share,'configured',lambda *args:dict(role_share.DEFAULT))
    monkeypatch.setattr(role_share,'usage',lambda *args:pytest.fail('Required readiness cannot spend optional reflection shares'))
    assert role_share.reason(tmp_path,3,job,dict(next_action='finish'),'A',evidence_return=target) is None
    assert role_share.reason(tmp_path,3,job,dict(next_action='finish'),'A')=='role_repeat_without_evidence'



def test_no_artifact_selected_fork_preserves_consumed_correction_and_validates_parent(tmp_path):
    from scidiscovery.research_loop import fork_runtime, fork_service, fork_sources
    parent=v2(tmp_path/'source');chain(parent)
    first=review(parent);publish(parent,first,request=copy.deepcopy(REQUEST))
    correction=c.build_job(parent,4,'W',[1,2,3],
        evidence_return=delivery_review.make('delivery_correction',3,1))
    finish(parent,4,'W',[1,2,3],text='The single bounded correction remains scientifically insufficient.')
    selected=review(parent,step=5,source=4,used=1,parents=[1,4])
    assert not selected['direct_materials']
    with controller._writer(parent):
        pass
    child=tmp_path/'child';allowance=dict(max_tokens=1000,max_seconds=60,max_steps=3)
    preview=fork_service.preview(parent,5,child,budget=allowance)
    assert preview['status']=='fork_preview_ready',preview
    fork_service.create(parent,5,child,budget=allowance,
        expected_preview_id=preview['preview_id'],operation_id='consumed-delivery-correction')
    assert fork_sources.selected_task(child)['job']['evidence_return']==selected['evidence_return']
    admitted=fork_runtime.build_selected_job(child,5)
    assert delivery_review.binding(admitted)==delivery_review.make('delivery_review',4,1)
    assert 'continue_w' not in c.result_schema('A',**c.response_options(admitted))['properties']['next_action']['enum']
    base=c.settings(child);head=accepted_budget.read(child)
    c.load_validated_input_facts(child,c.workspace(child,5),admitted,base=base,policy=base,head=head)
    changed=dict(admitted,evidence_return=delivery_review.make('delivery_review',4,0))
    with pytest.raises(ValueError,match='exact|scope|allowance'):
        c.load_validated_input_facts(child,c.workspace(child,5),changed,base=base,policy=base,head=head)
    assert not (child/'native-evidence/005/native-intent.json').exists()
