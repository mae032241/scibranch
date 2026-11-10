"""W evidence consultation traverses real tasks, seals and accepted publication."""
import copy
from pathlib import Path
from unittest.mock import patch

import pytest

from scidiscovery.research_loop import (
    accepted_budget, backends, budget, continuation, contracts as c, controller,
    delivery_review, handoff_state, supervision,
)
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_adaptive_a_budget import _prepare_publication_instance, _publish_synthetic

QUESTION = 'Does the exact retained source distinguish the conflicting residual explanations?'


def instance(tmp_path, **changes):
    return _prepare_publication_instance(tmp_path, search_policy=None, supervision_policy=None, **changes)


def publish(root, job, *, tokens=30, files=None, **authored):
    output=c.host_record(c.workspace(root,job['step']))/'science-output.json'
    write=c.write
    def original(path,value):
        if Path(path)==output:value=dict(value,**authored)
        return write(path,value)
    for name,value in (files or {}).items():
        target=c.workspace(root,job['step'])/'artifacts'/name
        target.parent.mkdir(parents=True,exist_ok=True);target.write_text(value)
    from scidiscovery.research_loop import profiles
    anchor=budget.accept_node(root,job)
    intent=dict(step=job['step'],task_ref=c.record_ref(root/'jobs'/f'{job["step"]:03d}.json'),
        **profiles.binding(c.settings(root,job),job),**(dict(budget_slice=anchor) if anchor is not None else {}))
    record=c.host_record(c.workspace(root,job['step']))
    c.write(record/'native-intent.json',intent)
    accepted_budget.accept_native(root,job['step'],record,intent)
    with patch.object(c,'write',original):
        _publish_synthetic(root,job,tokens=tokens,intent=intent,record=record)
    c.seal_result(root,job['step'])
    return handoff_state.read_sealed(root,job['step'])


def next_job(root, route, step, *, supervision_request=None):
    return c.build_job(root,step,route.role,list(route.parents),instruction=route.instruction,
        evidence_return=route.evidence_return,supervision_request=supervision_request)


@pytest.mark.parametrize('missing',[False,True])
def test_real_worker_evidence_return_keeps_question_materials_budget_and_continuity(tmp_path,missing):
    root=instance(tmp_path)
    worker=c.build_job(root,1,'W',[])
    request=publish(root,worker,stage_intent='extract',consultation_question=QUESTION,
        files={'candidate.py':'retained runnable candidate','residual.txt':'exact conflicting residual'})
    original=(root/'native-evidence/001/science-output.json').read_bytes()
    proposed=controller._route(root,1,'W',request,worker,None,None)
    assert proposed.role=='E' and proposed.evidence_return==dict(role='W',step=1)
    extractor=next_job(root,proposed,2)
    assert extractor['purpose']=='worker_consultation' and extractor['consultation_question']==QUESTION
    view=c.read(c.workspace(root,2)/'science-task.json')
    assert view['consultation']['question']==QUESTION and view['consultation']['return_role']=='W'
    before=copy.deepcopy(extractor['direct_materials'])
    returned=publish(root,extractor,scientific_result='Missing original source; no value inferred.' if missing else 'The exact retained evidence answers the bounded question.',
        limitations=['Requested source missing'] if missing else ['Synthetic exact-source fixture'],
        implementation_selection=[],files={} if missing else {'evidence.txt':'bounded extracted source'})
    result=controller._route(root,2,'E',returned,extractor,proposed.current_h,proposed.current_w)
    assert result.role=='W' and result.parents==(1,2) and result.evidence_return is None
    successor=next_job(root,result,3)
    actual={row['path']:row for row in successor['direct_materials']}
    assert all(actual[row['path']]==row for row in before)
    assert successor['worker_continuation']['requester_step']==1
    assert successor['worker_continuation']['advice_step']==2
    assert (root/'native-evidence/001/science-output.json').read_bytes()==original
    assert accepted_budget.read(root)['usage']['input_tokens']==60
    policy=c.settings(root,successor)
    plan=continuation.consultation_launch(root,successor,settings=policy,
        backend=backends.selected(policy),profile={})
    assert plan['worker_continuation']['fallback_reason']=='consultation_resume_unsupported_backend'
    assert c.build_job(root,3,'W',list(result.parents),instruction=result.instruction)==successor
    assert c.validate(c.workspace(root,2))['role']=='E'


@pytest.mark.parametrize('mutation',['blank_question','fake_requester','changed_question','changed_ref','final_request','correction_request'])
def test_worker_evidence_scope_cannot_be_forged_or_expand_final_correction(tmp_path,mutation):
    root=instance(tmp_path)
    worker=c.build_job(root,1,'W',[])
    if mutation=='blank_question':
        with pytest.raises(OutputRepairRequired,match='consultation_question'):
            publish(root,worker,stage_intent='extract',consultation_question='  ')
        assert not (root/'native-evidence/001/result-identity.json').exists()
        return
    request=publish(root,worker,stage_intent='extract' if mutation!='fake_requester' else 'handoff',consultation_question=QUESTION)
    if mutation in {'final_request','correction_request'}:
        target=delivery_review.make('delivery_review' if mutation=='final_request' else 'delivery_correction',1,0 if mutation=='final_request' else 1)
        with pytest.raises(ValueError):c.build_job(root,2,'E',[1],evidence_return=target)
        return
    if mutation=='fake_requester':
        with pytest.raises(ValueError,match='requester'):
            c.build_job(root,2,'E',[1],evidence_return=dict(role='W',step=1))
        return
    route=controller._route(root,1,'W',request,worker,None,None)
    extractor=next_job(root,route,2)
    changed=copy.deepcopy(extractor)
    if mutation=='changed_question':changed['consultation_question']='Different evidence question'
    else:changed['direct_refs']=[]
    with pytest.raises(ValueError):
        c._validate_worker_consultation(changed,dict(job=worker,payload=request))
    assert c.read_job(root/'jobs/002.json')==extractor


def test_hypothesis_consultation_still_returns_same_requesting_worker(tmp_path):
    root=instance(tmp_path)
    worker=c.build_job(root,1,'W',[])
    request=publish(root,worker,stage_intent='revise_h',consultation_question=QUESTION)
    route=controller._route(root,1,'W',request,worker,None,None)
    consultant=next_job(root,route,2)
    advice=publish(root,consultant,next_action='wait',search_plan=None)
    returned=controller._route(root,2,'H',advice,consultant,route.current_h,route.current_w)
    successor=next_job(root,returned,3)
    assert successor['worker_continuation']['requester_step']==1
    assert 'original objective' in successor['instruction']


def test_nonconsulting_worker_can_seal_an_explicit_null_question(tmp_path):
    root=instance(tmp_path)
    worker=c.build_job(root,1,'W',[])
    response=publish(root,worker,stage_intent='handoff',consultation_question=None)
    assert response['consultation_question'] is None
    assert c.validate(c.workspace(root,1))['stage_intent']=='handoff'
