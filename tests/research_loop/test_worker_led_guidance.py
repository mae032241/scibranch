"""Versioned worker-led prompts reach actual frozen tasks, without model calls."""
import pytest

from scidiscovery.research_loop import backends, contracts as c, guidance, native
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_result_schema import rendered_contract
from test_scientific_guidance import prepare


@pytest.mark.parametrize('role', ['H', 'W', 'A', 'E'])
@pytest.mark.parametrize('backend_name', ['codex', 'protocol'])
def test_worker_led_roles_are_frozen_and_rendered(tmp_path, role, backend_name):
    root=prepare(tmp_path,guidance.WORKER_LED,research_strategy_version=1)
    job=c.build_job(root,1,role,[],instruction='Resolve the actual objective using retained evidence.')
    task=c.read(c.workspace(root,1)/'science-task.json')
    assert c.settings(root)['research_strategy_version']==job['research_strategy_version']==1
    assert job['native_role_instruction']==guidance.WORKER_LED[role]
    assert task.get('role_contract','')==job['role_purpose_instruction']==guidance.role_contract(job)
    assert 'research_strategy_version' not in task
    prompt,instruction,_=native.render_scientific_request(job,task,{},backend_name=backend_name,
        instruction_transport='prompt')
    assert prompt.count(guidance.WORKER_LED[role])==1
    if role in {'H','W'}:
        assert guidance.STAGE_PLANNING[role] not in prompt
    if role=='H':
        assert guidance.WORKER_LED_PLANNING['H'] in task['role_contract']
        assert guidance.WORKER_LED_PLANNING['H'] not in prompt+instruction
        assert 'representations or problem decompositions' in prompt
        assert 'Label speculation' in prompt
        assert 'No mandatory method-family coverage' in task['role_contract']
    elif role=='W':
        assert 'score/validation-object alignment' in prompt
        assert 'negative results' in prompt
        assert 'review=ordinary A reflection' in task['stage_response']
        assert 'Advice returns W; no grant' in task['stage_response']
        assert 'decision=ready to close: protected Final A required before delivery' in task['stage_response']
        assert 'exploratory/adaptive evidence from independent final checks' in prompt
    elif role=='A':
        contract=task['role_contract']
        assert 'local lock-in, target drift, ignored evidence' in prompt
        assert 'productive deepening from stagnation' in prompt
        assert 'continue_w with decision_request=null' in contract
        assert 'no material issue' in prompt
        assert 'method prescriptions' in prompt and 'final-proof demands for exploratory work' in prompt
        assert 'deliver/finish requests mandatory final A assessment' in contract
        assert 'A owns final scientific/delivery decisions' not in prompt+instruction
        assert 'deliver eligible candidate or finish' not in prompt
        assert 'final A assessment' in task['delivery_selection']
    originals={path:path.read_bytes() for path in (root/'settings.json',root/'jobs/001.json',
        c.workspace(root,1)/'science-task.json')}
    assert c.build_job(root,1,role,[],instruction=job['instruction'])==job
    assert all(path.read_bytes()==data for path,data in originals.items())


@pytest.mark.parametrize('version', [None, 1])
@pytest.mark.parametrize('advice', [None, {role:'Custom scientific instruction for '+role for role in guidance.DEFAULT}])
def test_strategy_preserves_custom_literals_and_original_marker_absence(tmp_path, version, advice, monkeypatch):
    root=prepare(tmp_path,advice,research_strategy_version=version)
    job=c.build_job(root,1,'W',[])
    expected=(advice['W'] if advice is not None else
        (guidance.WORKER_LED if version==1 else guidance.OPPORTUNITY)['W'])
    assert job['native_role_instruction']==expected
    assert ('research_strategy_version' in job)==(version==1)
    assert ('research_strategy_version' in c.settings(root))==(version==1)
    paths=(root/'settings.json',root/'jobs/001.json',c.workspace(root,1)/'science-task.json')
    originals={path:path.read_bytes() for path in paths}
    monkeypatch.setitem(guidance.WORKER_LED,'W','Future worker-led advice changes no original.')
    monkeypatch.setitem(guidance.OPPORTUNITY,'W','Future legacy advice changes no original.')
    assert c.build_job(root,1,'W',[])==job
    assert all(path.read_bytes()==data for path,data in originals.items())


@pytest.mark.parametrize('flags', [dict(formal_review=True),dict(purpose='closeout'),
    dict(final_only=True),dict(final_report=True)])
def test_worker_led_final_and_formal_assessments_keep_their_existing_authority(flags):
    legacy=dict(role='A',**flags)
    current=dict(legacy,research_strategy_version=1)
    assert guidance.role_contract(current)==guidance.role_contract(legacy)
    assert 'decision_request' not in c.response_fields(current,{})


def test_worker_led_consultations_and_supervision_do_not_invent_problems():
    consultation=guidance.role_contract(dict(role='A',purpose='worker_consultation',research_strategy_version=1))
    assert 'no material issue' in guidance.WORKER_LED['A'] and 'then return to W' in consultation
    assert 'no global allocation, candidate choice, formal verdict or grant' in consultation
    contract=guidance.role_contract(dict(role='A',research_strategy_version=1,supervision_request={}))
    assert 'continue_existing=continue_w without decision_request' in contract
    assert 'finalize=finish/deliver requests final assessment' in contract
    assert 'question,change_criterion' in contract


def test_worker_led_guidance_fits_existing_frozen_bound():
    assert guidance.policy(guidance.WORKER_LED)==guidance.WORKER_LED


@pytest.mark.parametrize('backend_name',['codex','protocol'])
@pytest.mark.parametrize('role,scope,branch',[
    ('H','ordinary','ordinary'),('H','consultation','ordinary'),
    ('W','ordinary','ordinary'),('W','ordinary','fragment'),('W','ordinary','repair'),
    ('W','max_fields','ordinary'),('W','max_fields','fragment'),('W','max_fields','repair'),
    ('A','ordinary','ordinary'),('A','ordinary','automatic'),('A','ordinary','repair'),
    ('A','consultation','ordinary'),('A','supervision','ordinary'),
    ('A','closeout','ordinary'),('A','final','ordinary'),('E','ordinary','ordinary'),
])
def test_worker_led_actual_fixed_contract_fits_existing_bound(tmp_path,backend_name,role,scope,branch):
    job=dict(role=role,handoff_protocol=c.HANDOFF_PROTOCOL,research_strategy_version=1,
        native_role_instruction=guidance.WORKER_LED[role],formal_review_version=1)
    if role in {'H','W'}:job['search']={}
    if role=='W':
        job.update(worker_fragment_policy=dict(context='same_session'),
            scientific_work_unit=max(guidance.WORK_UNITS.values(),key=lambda value:len(value.encode())))
    if scope=='consultation':job['purpose']='worker_consultation'
    elif scope=='supervision':
        job['supervision_request']=dict(version=1,cutoff=dict(total_rounds=0,source_step=None,round_id=None),
            due_reasons=['fixture'],budget_fraction=None,automatic=True,final=False)
    elif scope in {'closeout','final'}:job.update(purpose='closeout',search=None)
    if scope=='final':job.update(final_only=True,delivery_sources=dict(items=[{}]))
    job['scientific_response_fields']=c.response_fields(job,{})
    if scope=='max_fields':
        job['scientific_response_fields']=list(dict.fromkeys([*job['scientific_response_fields'],
            'mechanism_findings','candidate_nominations']))
    job['role_purpose_instruction']=guidance.role_contract(job)
    task=c.response_instructions(job)
    if scope=='max_fields':
        assert all(name+'?:' in task['stage_response'] for name in
            ('mechanism_findings','exploration_rounds','candidate_nominations'))
    backend=backends.selected(dict(native_backend=backend_name,
        native_launcher_argv=['codex'] if backend_name=='codex' else ['independent'],
        native_options={} if backend_name=='codex' else None),
        schema_transport='prompt' if backend_name=='codex' else None)
    intent={};remaining=None
    if branch=='automatic':intent['continuation_kind']='automatic_recovery';remaining={}
    elif branch=='fragment':intent['continuation_kind']='fragment';remaining={}
    elif branch=='repair':
        repair_field='work_repair' if role=='W' else 'output_repair'
        intent[repair_field]=OutputRepairRequired('candidate_recommendation_missing','D'*512).request()
        remaining={}
    context=dict(remaining=remaining,final_return=scope=='final',
        budget_status=dict(shared_seconds_remaining=None,shared_observed_tokens_remaining=None),
        execution_interface=dict(capabilities={},result_protocol='local-result-v1',
            executor='local_execution:command') if role=='W' else None,
        managed_command=['experiment'] if role=='W' else None)
    size,_,_,_=rendered_contract(tmp_path,job,task,intent,backend,**context)
    if branch=='repair':
        empty=dict(intent,**{repair_field:dict(intent[repair_field],diagnostic='')})
        fixed_size,_,_,_=rendered_contract(tmp_path,job,task,empty,backend,**context)
        assert size-fixed_size<=512
        assert fixed_size+512<=c.MAX_FIXED_RESPONSE_BYTES,(role,scope,backend_name,branch,fixed_size)
    assert size<=c.MAX_FIXED_RESPONSE_BYTES==3072,(role,scope,backend_name,branch,size)
