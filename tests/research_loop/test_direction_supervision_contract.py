"""One scoped direction decision contract across native scientific backends."""
import copy

import pytest

from scidiscovery.research_loop import budget, budget_notice, contracts as c, decision, guidance
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_scientific_guidance import prepare


def scope():
    return dict(version=1,cutoff=dict(total_rounds=0,source_step=None,round_id=None),
        due_reasons=['ordinary_assessment'],budget_fraction=None,automatic=False,final=False)


def assessment(**changes):
    return dict(role='A',step=1,handoff_protocol=c.HANDOFF_PROTOCOL,supervision_request=scope(),
        scientific_response_fields=['decision_request','supervision_result'],**changes)


def evidence_request():
    return dict(decision='mechanism',question='Does the target-condition observation separate the retained explanations?',
        change_criterion='A contradiction reopens the explanation; agreement retains the current work.')


@pytest.mark.parametrize('backend_name',['codex','protocol'])
def test_scoped_assessment_freezes_one_common_contract_and_preserves_custom_literal(tmp_path,backend_name):
    from scidiscovery.research_loop import native
    literal='Keep this exact custom A scientific instruction literal.'
    root=prepare(tmp_path,{**guidance.DEFAULT,'A':literal})
    request=scope()
    job=c.build_job(root,1,'A',[],instruction='Judge direction-level progress.',supervision_request=request)
    work=c.workspace(root,1)
    task=c.read(work/'science-task.json')
    originals={path:path.read_bytes() for path in (root/'jobs/001.json',work/'science-task.json')}
    assert guidance.purpose(job)=='assessment' and job.get('purpose') is None
    assert task['supervision_request']==job['supervision_request']==request
    assert task['role_contract']==guidance.role_contract(job)
    assert 'premature convergence' in task['role_contract']
    assert 'continue_existing=continue_w without decision_request' in task['role_contract']
    assert 'W chooses methods; H reconstructs mechanisms' in task['role_contract']
    assert 'mechanical checks/W' in task['role_contract']
    assert 'supervision_result' in job['scientific_response_fields']
    prompt,instruction,projection=native.render_scientific_request(job,task,{},backend_name=backend_name,
        instruction_transport='prompt')
    assert instruction.startswith(literal+'\n') and prompt.count(literal)==1
    assert projection==c.result_schema('A',**c.response_options(job))
    assert 'supervision_result' not in projection['required']
    assert c.build_job(root,1,'A',[],instruction=job['instruction'])==job
    assert c.build_job(root,1,'A',[],instruction=job['instruction'],supervision_request=copy.deepcopy(request))==job
    changed=copy.deepcopy(request);changed['automatic']=True
    with pytest.raises(ValueError,match='Frozen scientific task changed'):
        c.build_job(root,1,'A',[],instruction=job['instruction'],supervision_request=changed)
    assert all(path.read_bytes()==original for path,original in originals.items())


@pytest.mark.parametrize('conflict',[
    dict(author_correction={'reason':'fixture'}),dict(correction_request={'reason':'fixture'}),
    dict(engineering_repair={'reason':'fixture'}),dict(purpose='worker_consultation'),
    dict(purpose='formal_review'),dict(purpose='closeout'),dict(final_report={'reason':'fixture'}),
    dict(evidence_return={'kind':'formal_review'}),
])
def test_scoped_freeze_rejects_conflicting_authority_without_changing_original(tmp_path,conflict):
    root=prepare(tmp_path)
    job=c.build_job(root,1,'A',[],supervision_request=scope())
    original=(root/'jobs/001.json').read_bytes()
    with pytest.raises(ValueError,match='ordinary A assessment'):
        c.build_job(root,1,'A',[],supervision_request=scope(),**conflict)
    assert (root/'jobs/001.json').read_bytes()==original
    assert c.build_job(root,1,'A',[])==job


@pytest.mark.parametrize('result,action,authored_request',[
    ('continue_existing','continue_w',None),
    ('request_evidence','continue_w',True),
    ('request_evidence','revise_h',True),
    ('request_evidence','extract',True),
    ('finalize','finish',None),
    ('finalize','deliver',None),
])
def test_direction_decision_validator_consumes_supplied_values_only(monkeypatch,result,action,authored_request):
    monkeypatch.setattr(c,'read',lambda *args:pytest.fail('Pure decision validation must not read originals'))
    payload=dict(supervision_result=result,next_action=action,scientific_result='The retained evidence supports this direction decision.')
    if authored_request:payload['decision_request']=evidence_request()
    before=copy.deepcopy(payload)
    assert decision.validate_supervision(assessment(),payload)==result
    assert decision.require_request(assessment(),payload)==(payload.get('decision_request'))
    assert payload==before


@pytest.mark.parametrize('payload',[
    dict(next_action='continue_w'),
    dict(next_action='continue_w',supervision_result='invented'),
    dict(next_action='continue_w',supervision_result=['continue_existing']),
    dict(next_action='revise_h',supervision_result='continue_existing'),
    dict(next_action='continue_w',supervision_result='continue_existing',decision_request=evidence_request()),
    dict(next_action='continue_w',supervision_result='request_evidence'),
    dict(next_action='extract',supervision_result='request_evidence',
        decision_request=dict(decision='mechanism',question=' ',change_criterion='A distinguishing result')),
    dict(next_action='finish',supervision_result='request_evidence',decision_request=evidence_request()),
    dict(next_action='continue_w',supervision_result='finalize'),
    dict(next_action='wait',supervision_result='finalize'),
])
def test_inconsistent_scoped_decisions_require_same_original_output_repair(payload):
    payload=dict(payload,scientific_result='A retained direction-level judgment.')
    with pytest.raises(OutputRepairRequired) as error:decision.require_request(assessment(),payload)
    assert error.value.code=='a_supervision_result_inconsistent'
    request=error.value.request()
    assert 'same A output' in request['diagnostic']
    assert 'no new research' in request['diagnostic']


@pytest.mark.parametrize('science',[None,'',' ','\n\t'])
def test_blank_scoped_judgment_requires_same_original_repair_before_any_new_review(science):
    payload=dict(supervision_result='continue_existing',next_action='continue_w',scientific_result=science)
    with pytest.raises(OutputRepairRequired) as error:decision.require_request(assessment(),payload)
    assert error.value.code=='a_supervision_result_inconsistent'
    assert 'nonblank scientific_result' in error.value.request()['diagnostic']


@pytest.mark.parametrize('flags',[dict(purpose='worker_consultation'),dict(formal_review=True),
    dict(purpose='formal_review'),dict(purpose='closeout'),dict(final_only=True),dict(final_report=True)])
def test_nonordinary_scopes_keep_their_existing_authority(flags):
    job=assessment(**flags)
    assert decision.validate_supervision(job,dict(next_action='continue_w')) is None
    assert decision.require_request(job,dict(next_action='continue_w')) is None
    assert 'supervision_result' not in c.response_fields(job,{})


def test_ordinary_unscoped_further_work_still_requires_a_concrete_request():
    with pytest.raises(OutputRepairRequired) as error:
        decision.require_request(dict(role='A'),dict(next_action='continue_w',supervision_result='continue_existing'))
    assert error.value.code=='a_decision_request_incomplete'


@pytest.mark.parametrize('rounds',[None,[],[dict(mechanism='h001.m1',question='Which residual changed?',
    call_ids=['managed-call-1'],finding='The matched residual remains.')],['malformed'],42])
def test_optional_round_reporting_never_adds_a_transport_or_repair_gate(tmp_path,rounds):
    job=dict(role='W',handoff_protocol=c.HANDOFF_PROTOCOL)
    fields=c.response_fields(job,{})
    assert 'exploration_rounds' in fields
    job['scientific_response_fields']=fields
    payload=dict(summary='Retained finding',scientific_result='Known evidence with its limits.',limitations=[],
        stage_intent='handoff',exploration_rounds=rounds)
    raw=tmp_path/'science-output.json';c.write(raw,payload)
    assert c.science_payload(raw,'W',**c.response_options(job))==payload
    assert c.scientific_field(job,payload,'exploration_rounds')==(rounds if isinstance(rounds,list) else None)
    assert not c.scientific_diagnostics(job,payload)
    assert 'exploration_rounds?:[{mechanism,question,call_ids:[],finding}]' in c.response_instructions(job)['stage_response']


@pytest.mark.parametrize('backend_name',['codex','protocol'])
@pytest.mark.parametrize('role',['A','W'])
@pytest.mark.parametrize('repair',[False,True])
def test_common_default_response_contract_keeps_the_fixed_byte_cap(tmp_path,backend_name,role,repair):
    from scidiscovery.research_loop import backends
    from test_result_schema import rendered_contract
    job=dict(role=role,handoff_protocol=c.HANDOFF_PROTOCOL,native_role_instruction=guidance.DEFAULT[role])
    if role=='A':job.update(supervision_request=scope(),scientific_response_fields=['decision_request','supervision_result'])
    else:job.update(worker_fragment_policy=dict(context='same_session'),
        scientific_work_unit=max(guidance.WORK_UNITS.values(),key=lambda text:len(text.encode())))
    backend=backends.selected(dict(native_backend=backend_name,
        native_launcher_argv=['codex'] if backend_name=='codex' else ['independent'],
        native_options={} if backend_name=='codex' else None),schema_transport='prompt' if backend_name=='codex' else None)
    intent={}
    if repair:intent['work_repair' if role=='W' else 'output_repair']=OutputRepairRequired('a_supervision_result_inconsistent','D'*512).request()
    size,*_=rendered_contract(tmp_path,job,c.response_instructions(job),intent,backend,remaining={} if repair else None,
        execution_interface=dict(capabilities={},result_protocol='local-result-v1',executor='local_execution:command') if role=='W' else None,
        managed_command=['experiment'] if role=='W' else None,
        budget_status=dict(shared_seconds_remaining=0,shared_observed_tokens_remaining=0))
    assert size<=c.MAX_FIXED_RESPONSE_BYTES==3072


def test_seal_semantics_and_output_repair_share_the_pure_direction_validator(tmp_path,monkeypatch):
    from scidiscovery.research_loop import formal_review, semantic
    from types import SimpleNamespace
    job=assessment()
    inventory=SimpleNamespace(interpretation=dict(focus=None,allocation=None,diagnostics=[]))
    monkeypatch.setattr(formal_review,'validate_choice',lambda *args,**kwargs:None)
    monkeypatch.setattr(semantic,'author_choices',lambda *args,**kwargs:(inventory.interpretation,[]))
    with pytest.raises(OutputRepairRequired) as error:
        semantic.outcome(tmp_path,job,dict(next_action='continue_w'),inventory=inventory)
    assert error.value.code=='a_supervision_result_inconsistent'


def test_output_repair_reuses_scoped_original_and_frozen_cutoff(tmp_path,monkeypatch):
    from scidiscovery.research_loop import candidate_refs, continuation, formal_review, semantic
    root=tmp_path/'instance';record=c.host_record(c.workspace(root,1))
    job=assessment(worker_view_version=1,lineage_projection_version=1,direct_materials=[],history=dict(nodes=[]))
    c.write(root/'settings.json',dict(handoff_protocol=c.HANDOFF_PROTOCOL,search_policy=None))
    c.write(root/'settings-identity.json',dict(path='settings.json'))
    c.write(root/'jobs/001.json',job)
    payload=dict(summary='Direction judgment',scientific_result='The current mechanism retains decision value.',
        limitations=[],next_action='continue_w',route_reason='Preserve useful unfinished work.')
    raw=record/'science-output.json';c.write(raw,payload)
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,usage=dict(unknown_steps=[]),raw_ref=c.file_ref(raw)))
    inventory=c.CurrentInventory(root,job,record,0,[],{},policy=dict(search_policy=None),
        science_available=True,payload=payload,interpretation=dict(focus=None,allocation=None,diagnostics=[]))
    monkeypatch.setattr(continuation,'validate_task',lambda *args,**kwargs:job)
    monkeypatch.setattr(candidate_refs,'control_payload',lambda job,science,**kwargs:science)
    monkeypatch.setattr(formal_review,'validate_choice',lambda *args,**kwargs:None)
    monkeypatch.setattr(semantic,'author_choices',lambda *args,**kwargs:(inventory.interpretation,[]))
    original=(root/'jobs/001.json').read_bytes()
    request=continuation.output_request(root,1,record,inventory=inventory,source_attempt=0)
    assert request['code']=='a_supervision_result_inconsistent'
    assert request['raw_ref']==c.file_ref(raw) and request['source_attempt']==0
    assert (root/'jobs/001.json').read_bytes()==original
    assert not (root/'jobs/002.json').exists()


@pytest.mark.parametrize('text',[budget_notice.WORKER_PROGRESS_TEXT,budget_notice.WORKER_CONTEXT_TEXT,
    budget_notice.WORKER_CONTEXT_UNKNOWN_TEXT])
def test_budget_notices_request_fresh_w_handoff_and_separate_direction_supervision(text):
    assert 'fresh W' in text and 'ledger/control due state' in text
    assert 'A review' not in text and 'independent A' not in text
    assert len(text.encode())<=budget_notice.MAX_NOTICE_BYTES


@pytest.mark.parametrize('tokens,seconds,expected',[(None,None,None),(20,None,.2),(None,40,.4),(20,40,.4),(120,40,1.2)])
def test_budget_fraction_is_pure_and_retains_unknowns_and_overrun(tokens,seconds,expected,monkeypatch):
    monkeypatch.setattr(c,'read',lambda *args:pytest.fail('Budget fraction must use supplied observations'))
    assert budget.supervision_fraction(initial_tokens=100,initial_seconds=100,
        observed_tokens=tokens,active_seconds=seconds)==expected
