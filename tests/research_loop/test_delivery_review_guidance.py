"""Frozen current readiness authority reaches real task packets and compact notices."""
import json
import subprocess
import sys

import pytest

from scidiscovery.research_loop import accepted_budget, backends, budget_notice, contracts as c
from scidiscovery.research_loop import delivery_review, guidance, native
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_history_handoff import finish
from test_result_schema import rendered_contract
from test_scientific_guidance import prepare


def backend(name):
    return backends.selected(dict(native_backend=name,
        native_launcher_argv=['codex'] if name=='codex' else ['independent'],
        native_options={} if name=='codex' else None),
        schema_transport='prompt' if name=='codex' else None)


def reviewed(root, step, parent, used):
    return c.build_job(root,step,'A',[parent],
        evidence_return=delivery_review.make('delivery_review',parent,used))


def correction_return(root, job):
    """Seal a declared correction fixture without launching any native work."""
    work=c.workspace(root,job['step']);record=c.host_record(work)
    intent=dict(task_ref=c.record_ref(root/'jobs'/f'{job["step"]:03d}.json'))
    c.write(record/'native-intent.json',intent)
    accepted_budget.accept_native(root,job['step'],record,intent)
    c.write(record/'science-output.json',dict(summary='Bounded correction',
        scientific_result='Retained observation resolves the assigned decision gap.',
        limitations=['Synthetic fixture, no scientific qualification'],
        stage_intent='decision',search_plan=None,implementation_selection=None))
    c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,native_started=False,
        usage=native.usage(root),raw_ref=c.file_ref(record/'science-output.json')))
    c.seal_result(root,job['step'])


@pytest.mark.parametrize('role',['H','W','A','E'])
@pytest.mark.parametrize('backend_name',['codex','protocol'])
def test_prepared_current_preserves_worker_led_roles(tmp_path,role,backend_name):
    root=prepare(tmp_path,None,research_strategy_version=3)
    job=c.build_job(root,1,role,[])
    task=c.read(c.workspace(root,1)/'science-task.json')
    prompt,instruction,_=native.render_scientific_request(job,task,{},backend_name=backend_name,
        instruction_transport='prompt')
    assert job['research_strategy_version']==c.settings(root)['research_strategy_version']==3
    assert job['native_role_instruction']==guidance.ADVISORY_WORKER_FIRST[role]
    assert prompt.count(guidance.ADVISORY_WORKER_FIRST[role])==1
    if role=='A':
        assert 'continue_w with decision_request=null' in task['role_contract']
        assert 'productive deepening' in task['role_contract']
        assert 'deliver eligible candidate or finish' not in prompt
    if role=='W':
        assert 'pre-delivery' in task['stage_response']
        assert 'consultation_question' in task['stage_response']
        assert 'engineering_check: sample, no training/audit.' in prompt
    assert guidance.policy(guidance.ADVISORY_WORKER_FIRST)==guidance.ADVISORY_WORKER_FIRST


@pytest.mark.parametrize('backend_name',['codex','protocol'])
def test_prepared_readiness_then_one_correction_and_reassessment(tmp_path,backend_name):
    root=prepare(tmp_path,None,research_strategy_version=3)
    finish(root,1,'W',[],stage_intent='decision')
    first=reviewed(root,2,1,0)
    task=c.read(c.workspace(root,2)/'science-task.json')
    prompt,instruction,schema=native.render_scientific_request(first,task,{},
        backend_name=backend_name,instruction_transport='prompt')
    assert first.get('purpose') is None and not first.get('final_only')
    assert guidance.purpose(first)=='delivery_review'
    assert set(schema['properties']['next_action']['enum'])=={'deliver','finish','continue_w'}
    assert 'Availability is not scientific qualification' in task['role_contract']
    assert 'reject all' in task['role_contract'] and 'best-effort needs original task policy' in task['role_contract']
    assert 'one' in task['delivery_selection'] and 'decision_request' in task['delivery_selection']
    assert 'Goals remain.' not in prompt+instruction
    originals={path:path.read_bytes() for path in (root/'jobs/002.json',c.workspace(root,2)/'science-task.json')}
    assert reviewed(root,2,1,0)==first
    assert all(path.read_bytes()==data for path,data in originals.items())
    finish(root,2,'A',[1],action='continue_w')
    correction=c.build_job(root,3,'W',[2],
        evidence_return=delivery_review.make('delivery_correction',2,1))
    task=c.read(c.workspace(root,3)/'science-task.json')
    prompt,instruction,schema=native.render_scientific_request(correction,task,{},
        backend_name=backend_name,instruction_transport='prompt')
    assert guidance.purpose(correction)=='delivery_correction'
    assert set(schema['properties']['stage_intent']['enum'])=={'continue','handoff','decision'}
    assert schema['properties']['search_plan']==dict(type='null')
    assert 'original remaining budgets' in task['role_contract']
    assert 'broader research or consultation' in task['role_contract']
    assert 'Goals remain.' not in prompt+instruction and 'revise_h=H' not in task['stage_response']
    correction_return(root,correction)
    final=reviewed(root,4,3,1)
    task=c.read(c.workspace(root,4)/'science-task.json')
    _,_,schema=native.render_scientific_request(final,task,{},backend_name=backend_name,
        instruction_transport='prompt')
    assert set(schema['properties']['next_action']['enum'])=={'deliver','finish'}
    assert 'continue_w' not in schema['properties']['next_action']['enum']
    assert 'Correction consumed: no further work route.' in task['delivery_selection']


@pytest.mark.parametrize('backend_name',['codex','protocol'])
def test_protected_current_can_reject_nonempty_inventory_without_new_work(tmp_path,backend_name):
    job=dict(role='A',step=3,handoff_protocol=c.HANDOFF_PROTOCOL,research_strategy_version=3,advisory_scope_version=1,
        purpose='closeout',final_only=True,delivery_sources=dict(items=[dict(candidate='retained')]),
        native_role_instruction=guidance.ADVISORY_WORKER_FIRST['A'])
    task=c.response_instructions(job)
    prompt,instruction,schema=native.render_scientific_request(job,task,{},backend_name=backend_name,
        instruction_transport='prompt',final_return=True)
    assert set(schema['properties']['next_action']['enum'])=={'deliver','finish'}
    assert 'reject all' in task['role_contract'] and 'despite available candidates' in task['role_contract']
    assert 'no new role/audit/experiment' in prompt
    assert 'continue_w' not in schema['properties']['next_action']['enum']
    raw=tmp_path/'science-output.json'
    c.write(raw,dict(summary='No delivery',scientific_result='All retained candidates miss the objective.',
        limitations=['Synthetic finding'],next_action='finish',route_reason='Reject all',candidate_recommendation=None))
    assert c.science_payload(raw,'A',**c.response_options(job))['candidate_recommendation'] is None
    c.write(raw,dict(c.read(raw),next_action='deliver'))
    with pytest.raises(OutputRepairRequired,match='explicit existing candidate'):
        c.science_payload(raw,'A',**c.response_options(job))


@pytest.mark.parametrize('backend_name',['codex','protocol'])
@pytest.mark.parametrize('scope',['ordinary','review','reassessment','correction','protected'])
@pytest.mark.parametrize('repair',[False,True])
def test_current_fixed_contract_with_enabled_fields_keeps_existing_bound(tmp_path,backend_name,scope,repair):
    role='W' if scope=='correction' else 'A'
    job=dict(role=role,step=3,handoff_protocol=c.HANDOFF_PROTOCOL,research_strategy_version=3,advisory_scope_version=1,
        native_role_instruction=guidance.ADVISORY_WORKER_FIRST[role],formal_review_version=1)
    if scope in {'review','reassessment','correction'}:
        job['evidence_return']=delivery_review.make('delivery_correction' if role=='W' else 'delivery_review',
            2,0 if scope=='review' else 1)
    if scope=='protected':job.update(purpose='closeout',final_only=True,delivery_sources=dict(items=[{}]))
    if role=='W':job.update(search={},worker_fragment_policy=dict(version=2,context='same_session'),
        scientific_work_unit=max(guidance.WORK_UNITS.values(),key=lambda value:len(value.encode())))
    job['scientific_response_fields']=c.response_fields(job,{})
    if role=='W':job['scientific_response_fields']+=['mechanism_findings','candidate_nominations']
    job['role_purpose_instruction']=guidance.role_contract(job)
    task=c.response_instructions(job)
    intent={};remaining=None
    repair_field='work_repair' if role=='W' else 'output_repair'
    if repair:
        intent[repair_field]=OutputRepairRequired('candidate_recommendation_missing','D'*512).request()
        remaining={}
    context=dict(remaining=remaining,final_return=scope=='protected',
        budget_status=dict(shared_seconds_remaining=None,shared_observed_tokens_remaining=None),
        execution_interface=dict(capabilities={},result_protocol='local-result-v1',executor='local_execution:command') if role=='W' else None,
        managed_command=['experiment'] if role=='W' else None)
    size,_,_,_=rendered_contract(tmp_path,job,task,intent,backend(backend_name),**context)
    if repair:
        empty=dict(intent,**{repair_field:dict(intent[repair_field],diagnostic='')})
        fixed_size,_,_,_=rendered_contract(tmp_path,job,task,empty,backend(backend_name),**context)
        assert fixed_size+512<=c.MAX_FIXED_RESPONSE_BYTES
    assert size<=c.MAX_FIXED_RESPONSE_BYTES==3072


@pytest.mark.parametrize('version',[3])
def test_exhausted_ordinary_a_notice_returns_advice_to_worker(version):
    job=dict(role='A',research_strategy_version=version,advisory_scope_version=1)
    text=budget_notice.closeout_notice(job,{},dimension='token',target_reached=False,
        shared_reached=True,shared_exhausted=True)['text']
    assert 'return advice with continue_w and decision_request=null' in text
    assert 'W judges it; no direct work or finalization' in text
    assert len(text.encode())<=200


@pytest.mark.process_e2e
@pytest.mark.parametrize('scope,used,exhausted',[
    ('delivery_review',0,False),('delivery_review',1,False),
    ('delivery_review',0,True),('delivery_correction',1,False),('delivery_correction',1,True)])
def test_scoped_notice_survives_actual_staged_stdlib_hook(tmp_path,scope,used,exhausted):
    job=dict(role='A' if scope=='delivery_review' else 'W',step=3,research_strategy_version=3,advisory_scope_version=1,
        evidence_return=delivery_review.make(scope,2,used))
    alert=budget_notice.closeout_notice(job,{},dimension='token',target_reached=True,
        shared_reached=exhausted,shared_exhausted=exhausted)
    assert len(alert['text'].encode())<=200
    if scope=='delivery_review':
        assert 'reject all' in alert['text']
        if exhausted:assert 'No correction/new W/E/extra FINAL' in alert['text']
        elif used:assert 'No second correction' in alert['text']
        else:assert 'one concrete correction' in alert['text']
    elif exhausted:assert 'No new work/replay' in alert['text']
    else:assert 'exact correction/gaps' in alert['text']
    c.write(tmp_path/'work/budget-status.json',dict(scope=budget_notice.SCOPE,updated_at_unix=123.,
        inflight_usage_unknown=True,target_state='reached',closeout_notices=dict(token=alert)))
    script=budget_notice.stage_helper(tmp_path)
    result=subprocess.run([sys.executable,'-I',str(script),'--work',str(tmp_path),'--attempt','0'],
        input='{}',text=True,capture_output=True,timeout=5)
    assert result.returncode==0,result.stderr
    value=json.loads(result.stdout)
    assert value['hookSpecificOutput']['additionalContext']==alert['text']
    assert budget_notice.hook_output(tmp_path,1) is None


def test_active_final_notice_never_requests_another_final():
    text=budget_notice.closeout_notice(dict(role='A',research_strategy_version=3),{},
        dimension='time',target_reached=True,shared_reached=True,final_return=True)['text']
    assert 'accepted final return' in text and 'No extra FINAL/new work' in text
    assert 'requests protected Final A' not in text
