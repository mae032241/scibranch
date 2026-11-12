"""Scientific advice is retained; actions resolve exact registered objects."""
import json

import pytest

from scidiscovery.research_loop import accepted_budget, candidate_refs, contracts as c, continuation, native, originals
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_history_handoff import finish, scene, source, selection_explanation
from test_portfolio_contract import raw_author


def response(candidate=None, implementations=None):
    return dict(summary='Bounded comparison',scientific_result='Existing observations remain limited.',limitations=[],
        implementation_selection=implementations,next_action='continue_w',route_reason='Continue within the declared scope',
        candidate_recommendation=candidate,selection_explanation=selection_explanation() if candidate is not None else None,
        search_review=None,progress_assessment=None,decision_request=None,portfolio_review=None)


@pytest.mark.parametrize('recommendation', [None, 'W002-C001', 'W002-I004', 'W002-C999', 'local-'+'a'*64])
def test_candidate_schema_leaves_membership_to_the_consuming_action(tmp_path, recommendation):
    schema = c.result_schema('A', advisory_assessment=True)
    assert schema['additionalProperties'] is True
    assert set(schema['properties']) == {'summary', 'scientific_result', 'limitations', 'next_action', 'route_reason'}
    payload = response(recommendation, ['W002-I004'])
    payload['extra_science'] = dict(candidate_ids=[recommendation], subject={'id': 'an authored observation'})
    raw = tmp_path/'response.json'
    c.write(raw, payload)
    original = raw.read_bytes()
    assert c.science_payload(raw, 'A', advisory_assessment=True) == payload
    assert raw.read_bytes() == original


def test_scientific_explanation_length_is_not_output_admission(tmp_path):
    raw=tmp_path/'response.json';payload=response()
    payload.update(summary='',scientific_result='A detailed result. '*2000,route_reason='',
        portfolio_review='Scientific opportunity cost. '*2000,
        decision_request=dict(decision='candidate',question='Question '*2000,change_criterion='Evidence '*2000))
    c.write(raw,payload)
    assert c.science_payload(raw,'A')==payload


@pytest.mark.parametrize('recommendation',['W002-C999',{'observations':['limited']}],ids=['unavailable-string','unstructured-advice'])
def test_task_schema_and_consumption_omit_unused_auxiliary_fields_but_preserve_raw_science(scene,recommendation):
    source(scene)
    job,_,record=raw_author(scene,parents=(2,));work=c.workspace(scene,3)
    schema=c.read(record/'science-result.schema.json')
    assert job['scientific_response_fields']==['decision_request']
    assert schema==c.result_schema('A',**c.response_options(job))
    instructions=c.response_instructions(job);task=c.read(work/'science-task.json')
    assert set(instructions)=={'role_contract','implementation_reuse'}
    assert {key:task[key] for key in instructions}==instructions
    assert not {'final_response','delivery_selection'} & set(task)
    assert schema['required']==list(schema['properties'])
    assert not {'search_review','progress_assessment','decision_request','portfolio_review','candidate_comparison'} & set(schema['properties'])
    payload=response(recommendation,['W002-I001'])
    payload.update(search_review=['an ordinary scientific note'],progress_assessment='Scientific uncertainty',
        portfolio_review={'observations':['limited']},
        candidate_comparison={'unstructured':'comparison'},extra_science=['Original data'],
        candidate_ids=[{'note':'scientific data'}],scientific_annotation={'id':'a scientific object','subject':{'candidate_ids':[{}]}})
    c.write(record/'science-output.json',payload)
    original=(record/'science-output.json').read_bytes()
    outcome=c.read(record/'native-outcome.json')
    c.write(record/'native-outcome.json',dict(outcome,raw_ref=c.file_ref(record/'science-output.json')))
    inventory=c.accept_current_inventory(scene,3)
    assert inventory.selected_inputs==[]  # Ordinary advice cannot select W's implementation.
    c.seal_result_from_inventory(scene,3,inventory=inventory)
    result=c.validate(work)
    assert all(result[key]==value for key,value in payload.items())
    seal=c.read(record/'result-identity.json')
    assert not seal.get('selected_inputs')
    subject=recommendation if isinstance(recommendation,str) else 'candidate_recommendation'
    assert dict(code='candidate_recommendation_unresolved',subject=subject) in seal['control_outcome']['diagnostics']
    assert (record/'science-output.json').read_bytes()==original
    successor=c.build_job(scene,4,'H',[3])
    next_work=c.workspace(scene,4);ref=successor['direct_refs'][0]
    public_path=originals.resolve_root_ref(scene,dict(ref,path=str(next_work/ref['path'])))
    assert public_path==record/'science-output.json'
    public=c.read(public_path)
    assert public['candidate_ids']==payload['candidate_ids']
    assert public['scientific_annotation']==payload['scientific_annotation']
    assert public['candidate_recommendation']==payload['candidate_recommendation']
    assert (record/'science-output.json').read_bytes()==original
    with pytest.raises(continuation.ResumeUnavailable,match='worker_resume_unsupported'):
        continuation.validate_launcher(scene)


def test_scientific_extra_identity_words_are_not_framework_reference_slots():
    original='local-'+'a'*64;mapping={original:'W002-C001'}
    extra=dict(id=original,subject=original,candidate_ids=[{'note':'original science'},original])
    payload=dict(candidate_recommendation=original,unstructured=extra)
    public=candidate_refs.public_science_fields(payload,mapping)
    assert public['candidate_recommendation']=='W002-C001'
    assert public['unstructured']==extra and payload['candidate_recommendation']==original


@pytest.mark.parametrize('invalid',['object','overflow'])
def test_invalid_explicit_implementation_selection_cannot_inherit_a_previous_choice(scene,invalid):
    source(scene)
    _,_,previous=finish(scene,3,'W',[2],stage_intent='handoff',selection=['W002-I001'])
    previous_raw=(previous/'science-output.json').read_bytes()
    job=c.build_job(scene,4,'W',[3]);work=c.workspace(scene,4);record=c.host_record(work)
    assert [row['label'] for row in job['selected_inputs']]==['W002-I001']
    payload=dict(summary='Partial conclusion',scientific_result='The explicit selection is unavailable.',limitations=[],
        implementation_selection={'invalid':'explicit choice'} if invalid=='object' else ['W002-I001','overflow-sentinel'],stage_intent='handoff')
    intent=dict(task_ref=c.record_ref(scene/'jobs/004.json'))
    c.write(record/'native-intent.json',intent)
    accepted_budget.accept_native(scene,4,record,intent)
    original=json.dumps(payload).replace('"overflow-sentinel"','1e999').encode()
    (record/'science-output.json').write_bytes(original)
    c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,native_started=False,usage=native.usage(scene),
        raw_ref=c.file_ref(record/'science-output.json')))
    inventory=c.accept_current_inventory(scene,4)
    assert inventory.selected_inputs==[]
    assert inventory.selection_diagnostics==[dict(code='implementation_unresolved',subject='unparseable explicit selection')]
    consumed=native.usage(scene)
    with pytest.raises(OutputRepairRequired,match='implementation_selection'):
        c.seal_result_from_inventory(scene,4,inventory=inventory)
    assert not (record/'result-identity.json').exists() and not (record/'result.json').exists()
    assert not (work/'conclusion.json').exists() and not (scene/'jobs/005.json').exists()
    assert (record/'science-output.json').read_bytes()==original
    assert (previous/'science-output.json').read_bytes()==previous_raw
    assert native.usage(scene)==consumed


@pytest.mark.parametrize('field',['extra_science','candidate_recommendation','surrogate_key'])
def test_unrepresentable_optional_field_keeps_exact_raw_and_bound_parent_original(scene,field):
    source(scene)
    _,_,record=raw_author(scene,parents=(2,));work=c.workspace(scene,3)
    payload=response()
    key='\ud800' if field=='surrogate_key' else field
    payload[key]='an ordinary note' if field=='surrogate_key' else ['overflow-sentinel']
    original=json.dumps(payload).replace('"overflow-sentinel"','1e999').encode()
    (record/'science-output.json').write_bytes(original)
    outcome=c.read(record/'native-outcome.json')
    c.write(record/'native-outcome.json',dict(outcome,raw_ref=c.file_ref(record/'science-output.json')))
    c.seal_result(scene,3);result=c.validate(work)
    assert result['summary']==payload['summary'] and key not in result
    if field=='candidate_recommendation':
        diagnostics=c.read(record/'result-identity.json')['control_outcome']['diagnostics']
        assert dict(code='candidate_recommendation_unresolved',subject='candidate_recommendation') in diagnostics
    note=next(value for value in result.values() if isinstance(value,dict) and value.get('kind')=='scientific_projection')
    assert note['diagnostics'][0]['code']=='unrepresentable_scientific_field'
    assert (record/'science-output.json').read_bytes()==original
    successor=c.build_job(scene,4,'H',[3]);next_work=c.workspace(scene,4)
    public=c.read(next_work/successor['direct_refs'][0]['path'])
    note=next(value for value in public.values() if isinstance(value,dict) and value.get('kind')=='scientific_projection')
    bound_original=originals.resolve_root_ref(scene,dict(path=str(next_work/note['original'])))
    assert bound_original==record/'science-output.json'
    assert bound_original.read_bytes()==original
    assert 'Infinity' not in (record/'result.json').read_text()
    c._validate_inputs(scene,next_work,successor)
    bound_original.chmod(0o600);bound_original.write_bytes(original+b' ')
    with pytest.raises(ValueError,match='changed'):
        c._validate_inputs(scene,next_work,successor)
