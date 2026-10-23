"""Scientific advice is retained; actions resolve exact registered objects."""
import json

import pytest

from scidiscovery.research_loop import candidate_refs, contracts as c, continuation, native
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_history_handoff import finish, scene, source, selection_explanation


def response(candidate=None, implementations=None):
    return dict(summary='Bounded comparison',scientific_result='Existing observations remain limited.',limitations=[],
        implementation_selection=implementations,next_action='continue_w',route_reason='Continue within the declared scope',
        candidate_recommendation=candidate,selection_explanation=selection_explanation() if candidate is not None else None,
        search_review=None,progress_assessment=None,decision_request=None,portfolio_review=None)


def candidate_job(count):
    identities = ['local-'+f'{index:064x}' for index in range(count)]
    references = {identity:f'W002-C{index+1:03d}' for index,identity in enumerate(identities)}
    return dict(role='A',worker_view_version=1,history=dict(candidate_ids=identities,candidate_references=references))


def test_unavailable_scientific_recommendation_does_not_discard_implementation_or_original(scene, tmp_path):
    source(scene)
    job = c.build_job(scene, 3, 'A', [2])
    schema = c.read(c.host_record(c.workspace(scene,3))/'science-result.schema.json')
    choice = schema['properties']['candidate_recommendation']
    assert choice['type'] == ['string','null'] and 'enum' not in choice
    raw = tmp_path/'author-response.json'
    c.write(raw,response('W002-I001',['W002-I001']))
    original = raw.read_bytes()
    assert c.science_payload(raw,'A',**c.response_options(job))['candidate_recommendation']=='W002-I001'
    assert raw.read_bytes() == original
    finish(scene,3,'A',[2],selection=['W002-I001'])
    result = c.validate(c.workspace(scene,3))
    assert result['candidate_recommendation'] is None
    assert result['implementation_selection'] == ['W002-I001']
    # This instance did not freeze a continuation grant; a field error adds none.
    with pytest.raises(continuation.ResumeUnavailable,match='worker_resume_unsupported'):
        continuation.validate_launcher(scene)


def test_candidate_schema_leaves_membership_to_the_consuming_action(tmp_path):
    job = candidate_job(2)
    options = c.response_options(job)
    schema = c.result_schema('A',**options)
    assert 'enum' not in schema['properties']['candidate_recommendation']
    raw = tmp_path/'response.json'
    for recommendation in (None,'W002-C001','W002-C002'):
        c.write(raw,response(recommendation,['W002-I004']))
        science = c.science_payload(raw,'A',**options)
        resolved = candidate_refs.control_payload(job,science)
        assert resolved['implementation_selection'] == ['W002-I004']
        if recommendation is not None:
            assert resolved['candidate_recommendation'] in job['history']['candidate_ids']
    for recommendation in ('W002-I004','W002-C003',job['history']['candidate_ids'][0]):
        c.write(raw,response(recommendation))
        assert c.science_payload(raw,'A',**options)['candidate_recommendation']==recommendation


def test_large_candidate_schema_is_bounded_and_unavailable_advice_preserves_original(tmp_path):
    job = candidate_job(1000)
    options = c.response_options(job)
    schema = c.result_schema('A',**options)
    field = schema['properties']['candidate_recommendation']
    assert 'enum' not in field and len(json.dumps(field)) < 1024
    raw = tmp_path/'response.json'
    c.write(raw,response('W002-C1000'))
    assert c.science_payload(raw,'A',**options)['candidate_recommendation'] == 'W002-C1000'
    c.write(raw,response('W002-C1001'))
    original = raw.read_bytes()
    assert c.science_payload(raw,'A',**options)['candidate_recommendation']=='W002-C1001'
    assert raw.read_bytes() == original


def test_unavailable_ordinary_advice_seals_with_diagnostic_and_complete_original(scene):
    source(scene)
    _,work,record=finish(scene,3,'A',[2],action='wait',recommendation='W002-C999')
    original=(record/'science-output.json').read_bytes()
    assert c.validate(work)['candidate_recommendation']=='W002-C999'
    outcome=c.read(record/'result-identity.json')['control_outcome']
    assert dict(code='candidate_recommendation_unresolved',subject='W002-C999') in outcome['diagnostics']
    c.build_job(scene,4,'H',[3])
    assert (record/'science-output.json').read_bytes()==original


def test_scientific_explanation_length_is_not_output_admission(tmp_path):
    raw=tmp_path/'response.json';payload=response()
    payload.update(summary='',scientific_result='A detailed result. '*2000,route_reason='',
        portfolio_review='Scientific opportunity cost. '*2000,
        decision_request=dict(decision='candidate',question='Question '*2000,change_criterion='Evidence '*2000))
    c.write(raw,payload)
    assert c.science_payload(raw,'A')==payload


def test_task_schema_and_consumption_omit_unused_auxiliary_fields_but_preserve_raw_science(scene):
    source(scene)
    job=c.build_job(scene,3,'A',[2]);work=c.workspace(scene,3);record=c.host_record(work)
    schema=c.read(record/'science-result.schema.json')
    assert job['scientific_response_fields']==[]
    assert schema==c.result_schema('A',**c.response_options(job))
    assert c.read(work/'science-task.json')['final_response']==list(schema['properties'])
    assert schema['required']==list(schema['properties'])
    assert not {'search_review','progress_assessment','decision_request','portfolio_review','candidate_comparison'} & set(schema['properties'])
    payload=response()
    payload.update(next_action='wait',search_review=['an ordinary scientific note'],progress_assessment='Scientific uncertainty',
        decision_request={'unstructured':'original question'},portfolio_review={'observations':['limited']},
        candidate_comparison={'unstructured':'comparison'},extra_science=['Original data'],
        candidate_ids=[{'note':'scientific data'}],scientific_annotation={'id':'a scientific object','subject':{'candidate_ids':[{}]}})
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(scene/'jobs/003.json')))
    c.write(record/'science-output.json',payload)
    original=(record/'science-output.json').read_bytes()
    c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,usage=native.usage(scene),
        raw_ref=c.file_ref(record/'science-output.json')))
    c.seal_result(scene,3)
    result=c.validate(work)
    assert all(result[key]==value for key,value in payload.items())
    assert (record/'science-output.json').read_bytes()==original
    successor=c.build_job(scene,4,'H',[3])
    public=c.read(c.workspace(scene,4)/successor['direct_refs'][0]['path'])
    assert public['candidate_ids']==payload['candidate_ids']
    assert public['scientific_annotation']==payload['scientific_annotation']


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
    finish(scene,3,'A',[2],selection=['W002-I001'])
    job=c.build_job(scene,4,'W',[3]);work=c.workspace(scene,4);record=c.host_record(work)
    assert job['selected_inputs']
    payload=dict(summary='Partial conclusion',scientific_result='The explicit selection is unavailable.',limitations=[],
        implementation_selection={'invalid':'explicit choice'} if invalid=='object' else ['overflow-sentinel'],stage_intent='review')
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(scene/'jobs/004.json')))
    (record/'science-output.json').write_text(json.dumps(payload).replace('"overflow-sentinel"','1e999'))
    c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,usage=native.usage(scene),
        raw_ref=c.file_ref(record/'science-output.json')))
    c.seal_result(scene,4)
    seal=c.read(record/'result-identity.json')
    assert seal['control_outcome']['selection_status']=='unresolved' and not seal.get('selected_inputs')
    result=c.validate(work)
    if invalid=='object':assert result['implementation_selection']==payload['implementation_selection']
    else:assert 'implementation_selection' not in result
    successor=c.build_job(scene,5,'H',[4])
    assert successor['input_selection_status']=='unresolved' and not successor.get('selected_inputs')


@pytest.mark.parametrize('field',['extra_science','candidate_recommendation','surrogate_key'])
def test_unrepresentable_optional_field_keeps_exact_raw_and_bound_parent_original(scene,field):
    source(scene)
    job=c.build_job(scene,3,'A',[2]);work=c.workspace(scene,3);record=c.host_record(work)
    payload=response();payload['next_action']='wait'
    key='\ud800' if field=='surrogate_key' else field
    payload[key]='an ordinary note' if field=='surrogate_key' else ['overflow-sentinel']
    original=json.dumps(payload).replace('"overflow-sentinel"','1e999').encode()
    (record/'science-output.json').write_bytes(original)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(scene/'jobs/003.json')))
    c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,usage=native.usage(scene),
        raw_ref=c.file_ref(record/'science-output.json')))
    c.seal_result(scene,3);result=c.validate(work)
    assert result['summary']==payload['summary'] and key not in result
    note=next(value for value in result.values() if isinstance(value,dict) and value.get('kind')=='scientific_projection')
    assert note['diagnostics'][0]['code']=='unrepresentable_scientific_field'
    assert (record/'science-output.json').read_bytes()==original
    successor=c.build_job(scene,4,'H',[3]);next_work=c.workspace(scene,4)
    public=c.read(next_work/successor['direct_refs'][0]['path'])
    note=next(value for value in public.values() if isinstance(value,dict) and value.get('kind')=='scientific_projection')
    original_copy=next_work/note['original']
    assert original_copy.read_bytes()==original
    assert 'Infinity' not in (record/'result.json').read_text()
    c._validate_inputs(scene,next_work,successor)
    original_copy.chmod(0o600);original_copy.write_bytes(original+b' ')
    with pytest.raises(ValueError,match='Bound scientific original changed'):
        c._validate_inputs(scene,next_work,successor)
