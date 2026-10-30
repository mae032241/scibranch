"""Scientific ownership and local budget advice use the real native request."""
import json

import pytest

from scidiscovery.research_loop import contracts as c, guidance, native
from test_history_handoff import finish
from test_scientific_guidance import prepare


def request_scene(tmp_path, role, *, advice=guidance.DEFAULT):
    root=prepare(tmp_path,advice)
    if role=='H':
        step,parents=1,[]
    else:
        finish(root,1,'H',[],action='experiment' if role=='W' else 'analyze')
        step,parents=2,[1]
    job=c.build_job(root,step,role,parents)
    work=c.workspace(root,step)
    return root,job,c.read(work/'science-task.json'),work


@pytest.mark.parametrize('role','HAW')
@pytest.mark.parametrize('transport',['prompt','developer'])
def test_role_planning_uses_control_allowance_without_rewriting_science(tmp_path,role,transport):
    root,job,task,work=request_scene(tmp_path,role)
    originals={path:path.read_bytes() for path in
        (root/'settings.json',root/'jobs'/f"{job['step']:03d}.json",work/'science-task.json')}
    status=dict(shared_seconds_remaining=9,shared_observed_tokens_remaining=17,
        node_budget=dict(max_observed_tokens=900000,max_seconds=120),
        exploration=dict(observed_tokens_remaining_upper_bound=17,
            active_seconds_available=9,token_usage_unknown=True))
    prompt,instruction,schema=native.render_scientific_request(job,task,{},backend_name='codex',
        instruction_transport=transport,budget_status=status)
    assert 'close with unmet goals if short' in prompt and 'Reuse evidence/code' in prompt
    assert prompt.count(guidance.STAGE_PLANNING[role])==1
    assert guidance.STAGE_PLANNING[role] not in instruction
    if role=='A':
        assert 'first-screen fairness, not total calls' in prompt
        assert 'zero alone is no denial/H trigger' in prompt
        assert 'continue_w for scoped science' in prompt
        assert 'revise_h for question/mechanism/constraint changes' in prompt
        assert 'No successor budgets/caps' in prompt
        assert 'H/A suggestions are not control limits' in prompt
    elif role=='H':
        assert 'Own questions/mechanisms/evidence and reevaluation' in prompt
        assert 'no downstream token/time budgets or execution/call/audit caps' in prompt
        assert 'Retain scientific counts, user/task requirements and native per-call caps' in prompt
    else:
        assert prompt.count('Budget own work before bulk reads/code')==1
        assert 'scientific stall, scope change or actual runtime refusal' in prompt
        assert 'within scientific scope/control allowance' in prompt
        assert 'H/A resource estimates add no runtime caps' in prompt
        assert 'Return to A for scientific stall' in prompt
        assert 'avoid bulk rereads' in prompt
        assert 'Return to independent A' in instruction
        assert 'A suggests methods' in instruction
    if role in 'HA':
        assert 'Control config/live allowances govern' in prompt
        assert 'Budget all work before routing' not in prompt
        assert 'Budget own work' not in prompt
        assert 'exhausted explicit H call cap' not in prompt
    assert 'partial/stale token billing, no grant; unknown tokens!=0' in prompt
    assert 'cached once' in prompt and 'Soft targets; call limits/permissions apply' in prompt
    assert json.JSONDecoder().raw_decode(prompt.splitlines()[-1])[0]==status
    assert schema==c.result_schema(role,job['handoff_protocol'],'selection' in job,**c.response_options(job))
    assert instruction.startswith(job['native_role_instruction']+'\n')
    assert all(path.read_bytes()==value for path,value in originals.items())


@pytest.mark.parametrize('variant',['output_repair','work_repair','output_only_wake','author','final','closeout','final_only','formal_review'])
def test_restricted_returns_do_not_suggest_new_budget_planning(tmp_path,variant):
    _,job,task,_=request_scene(tmp_path,'A' if variant in {'final','closeout','final_only','formal_review'} else 'W')
    intent={};options={}
    if variant=='output_repair':intent=dict(output_repair=dict(code='json',diagnostic='Return the existing result as JSON.'))
    elif variant=='work_repair':intent=dict(work_repair=dict(code='missing_work',diagnostic='Complete only the original assignment.'))
    elif variant=='output_only_wake':intent=dict(execution_wake=True);options['output_only_wake']=True
    elif variant=='author':options['author']=dict(facts='work/author-closeout-science.json',limits={})
    elif variant=='final':options['final_return']=True
    elif variant=='closeout':job['purpose']='closeout'
    elif variant=='final_only':job['final_only']=True
    elif variant=='formal_review':
        job['formal_review']={"subject": "original"}
        task['review_instruction']='Independently assess the exact original.'
    prompt,_,_=native.render_scientific_request(job,task,intent,backend_name='codex',
        instruction_transport='prompt',**options)
    assert 'Budget own work' not in prompt
    assert all(advice not in prompt for advice in guidance.STAGE_PLANNING.values())
    if variant=='final':assert 'Protected FINAL: conclude now; no new role/audit/experiment' in prompt


def test_custom_frozen_instruction_is_retained_with_launcher_level_budget_advice(tmp_path):
    custom={**guidance.DEFAULT,'H':'Retain all 25 required runs and the final eight unimproved audits. This finite stage permits exactly two calls.'}
    root,job,task,_=request_scene(tmp_path,'H',advice=custom)
    prompt,instruction,_=native.render_scientific_request(job,task,{},backend_name='codex',
        instruction_transport='prompt')
    assert instruction.startswith(custom['H']+'\n') and prompt.startswith(custom['H']+'\n')
    assert c.settings(root)['role_guidance']==custom
    assert 'Budget all work before routing' not in prompt
    assert 'Retain scientific counts, user/task requirements and native per-call caps' in prompt
    assert 'Control config/live allowances govern' in prompt
    assert prompt.count(guidance.STAGE_PLANNING['H'])==1


def test_worker_handoff_distinguishes_final_scientific_response_from_pending_wait(tmp_path):
    _,job,task,_=request_scene(tmp_path,'W')
    prompt,_,_=native.render_scientific_request(job,task,dict(execution_wake=True),backend_name='codex',
        instruction_transport='prompt')
    assert prompt.count('handoff.action:')==1
    assert 'poll pending originals' in prompt
    assert 'finalize_response=JSON' in prompt and 'return_control=wait' in prompt


@pytest.mark.parametrize('role','HWAE')
@pytest.mark.parametrize('repair',[False,True])
def test_default_fixed_contract_retains_room_for_full_repair_diagnostic(role,repair):
    # Include the applicable breadth/portfolio/final-coverage rules, rather
    # than measuring a small no-search task. Goal/data, concrete command and
    # budget observations are variable inputs outside this fixed contract.
    # Bound both official defaults, including jobs using the built-in fallback.
    job=dict(role=role,handoff_protocol=c.HANDOFF_PROTOCOL,
        native_role_instruction=max((guidance.DEFAULT[role],c.ROLE_INSTRUCTIONS[role]),key=lambda text:len(text.encode())),formal_review_version=1,
        scientific_response_fields=['portfolio_review','final_coverage'] if role=='A' else [])
    if role in 'HA':job['search']={}
    if role=='A':job.update(portfolio_context={},final_coverage_mechanisms=[{}])
    if role=='W':job['worker_fragment_policy']=dict(context='new_context')
    rules=c.response_instructions(job)
    intent=dict(output_repair=dict(code='output_layout_invalid',diagnostic='x'*512)) if repair else {}
    options={}
    if role=='W':options=dict(execution_interface=dict(capabilities={},result_protocol={}),managed_command=[])
    prompt,instruction,_=native.render_scientific_request(job,rules,intent,backend_name='codex',
        instruction_transport='developer',**options)
    # The developer transport keeps the instruction separate. Prompt transport
    # embeds those same bytes and must not count them a second time.
    embedded,_,_=native.render_scientific_request(job,rules,intent,backend_name='codex',
        instruction_transport='prompt',**options)
    assert embedded==instruction+'\n'+prompt
    fixed_bytes=len((instruction+'\n'+prompt).encode())+len(
        json.dumps(rules,ensure_ascii=False,separators=(',',':')).encode())
    total=fixed_bytes+(0 if repair else 512)
    assert total<=c.MAX_FIXED_RESPONSE_BYTES,f'{role} repair={repair}: {total} fixed bytes including diagnostic'
