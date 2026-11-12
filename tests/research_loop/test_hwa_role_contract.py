"""Purpose-specific H/W/A requests; inert fixtures grant no scientific verdict."""
import json

import pytest

from scidiscovery.research_loop import contracts as c, guidance, native
from test_history_handoff import finish
from test_scientific_guidance import prepare


@pytest.mark.parametrize('backend_name', ['codex', 'protocol'])
def test_prepared_ordinary_assessment_answers_w_without_global_planning(tmp_path, backend_name):
    root=prepare(tmp_path)
    finish(root,1,'H',[],action='experiment')
    finish(root,2,'W',[1])
    job=c.build_job(root,3,'A',[1,2],instruction='Does the retained candidate support the delivery claim?')
    work=c.workspace(root,3)
    task=c.read(work/'science-task.json')
    original=(root/'jobs/003.json').read_bytes()
    contract=job['role_purpose_instruction']
    assert task['role_contract']==contract==guidance.role_contract(job)
    assert job['research_strategy_version']==3 and job['advisory_scope_version']==1
    assert guidance.advisory_assessment(job)
    assert 'Return continue_w with decision_request=null' in contract
    assert 'W judges advice and chooses' in contract
    assert 'never a binding work order or finalization request' in contract
    assert 'delivery_selection' not in task
    assert c.result_schema('A',**c.response_options(job))['properties']['next_action']['enum']==['continue_w']
    assert not {'search_review','portfolio_review','final_coverage'} & set(job['scientific_response_fields'])
    assert not {'breadth_response','portfolio_response','final_coverage_response','portfolio_assessment'} & set(task)
    prompt,instruction,_=native.render_scientific_request(job,task,{},backend_name=backend_name,
        instruction_transport='prompt')
    assert 'Consultation:' not in prompt+instruction
    assert 'No dispositions/grants/main-line/candidate choice' not in prompt+instruction
    assert 'Complete the requested judgment' in prompt
    assert contract not in prompt  # Already supplied exactly once in the frozen task.
    assert c.build_job(root,3,'A',[1,2],instruction=job['instruction'])==job
    assert (root/'jobs/003.json').read_bytes()==original


@pytest.mark.parametrize('scope,flags', [
    ('assessment', {}),
    ('worker_consultation', dict(purpose='worker_consultation')),
    ('formal_review', dict(formal_review=dict(subject='exact fixture'))),
    ('closeout', dict(purpose='closeout')),
    ('protected_closeout', dict(purpose='closeout',final_only=True)),
])
@pytest.mark.parametrize('backend_name', ['codex', 'protocol'])
def test_bare_a_modes_retain_their_distinct_scope_and_frozen_custom_literal(scope, flags, backend_name):
    literal='Preserve this exact custom scientific assessment literal.'
    job=dict(role='A',handoff_protocol=c.HANDOFF_PROTOCOL,native_role_instruction=literal,**flags)
    contract=guidance.role_contract(job)
    job['role_purpose_instruction']=contract
    task=c.response_instructions(job)
    if scope=='formal_review':
        task['review_instruction']='Independently assess the exact retained subject.'
    prompt,instruction,_=native.render_scientific_request(job,task,{},backend_name=backend_name,
        instruction_transport='prompt')
    assert guidance.purpose(job)==scope
    assert not guidance.advisory_assessment(job)  # Bare rendering is not a prepared ordinary-A contract.
    if scope!='protected_closeout':
        assert guidance.ASSESSMENT_SCOPE in contract
        for text in ('file identity', 'interfaces', 'metric checks', 'exact originals/qualification/limits',
                'hidden scores', 'unique generator', 'perfect certainty'):
            assert text in contract
    if scope=='worker_consultation':
        assert 'meaningful judgment' in contract and 'then return to W' in contract
        assert 'candidate choice' in contract and 'deliver/finish/continue_w' not in contract
    elif scope=='formal_review':
        assert 'exact independent subject' in task['role_contract']
        assert contract not in instruction
        assert 'Do not edit the subject' in prompt
    elif scope=='protected_closeout':
        assert 'No new work' in contract and 'Exact deliver authorizes' in contract
    elif scope=='closeout':
        assert 'no new exploration' in contract
    else:
        assert 'continue_w with decision_request=null' in contract
        assert 'W chooses methods' in contract
    if scope!='formal_review':
        assert instruction.startswith(literal+'\n')
        assert prompt.count(literal)==1


def test_worker_owns_mechanism_handoff_and_invokes_h_a_only_when_useful():
    job=dict(role='W',handoff_protocol=c.HANDOFF_PROTOCOL,
        worker_fragment_policy=dict(context='same_session'))
    task=c.response_instructions(job)
    assert 'Own research/methods' in guidance.DEFAULT['W']
    assert 'judge H/A advice against evidence' in guidance.DEFAULT['W']
    assert 'W chooses E' in task['role_contract']
    assert 'revise_h=H advice;review=A advice' in task['stage_response']
    assert 'decision=pre-delivery Final A' in task['stage_response']
    assert 'Advice returns W; no grant' in task['stage_response']
    assert 'decision=formal A' not in json.dumps(task)
    assert "Answer W's concrete question" in guidance.DEFAULT['H']


@pytest.mark.parametrize('policy', [{}, dict(decision_contract_version=None), dict(decision_contract_version=1)])
def test_bare_a_exposes_required_decision_request_without_prepared_advisory_scope(policy):
    job=dict(role='A')
    fields=c.response_fields(job,policy)
    assert fields==['decision_request']
    job['scientific_response_fields']=fields
    request=dict(decision='mechanism',question='Which explanation survives the counterexample?',
        change_criterion='A matched control distinguishes the two retained explanations.')
    assert c.scientific_field(job,dict(decision_request=request),'decision_request')==request


@pytest.mark.parametrize('flags', [dict(purpose='worker_consultation'), dict(formal_review=True),
    dict(purpose='closeout'), dict(final_only=True), dict(final_report=True)])
def test_nonordinary_a_does_not_gain_a_further_work_request_field(flags):
    assert 'decision_request' not in c.response_fields(dict(role='A',**flags),{})


@pytest.mark.parametrize('previous', [None, 'An obsolete assessment instruction.'])
def test_formal_renderer_supplies_only_a_missing_or_changed_purpose_contract(previous):
    job=dict(role='A',handoff_protocol=c.HANDOFF_PROTOCOL,formal_review=True)
    contract=guidance.role_contract(job)
    task=dict(review_instruction='Independently assess the exact retained subject.')
    if previous is not None:task['role_contract']=previous
    _,instruction,_=native.render_scientific_request(job,task,{},backend_name='codex',
        instruction_transport='developer')
    assert instruction.count(contract)==1
    assert 'exact independent subject' in instruction
