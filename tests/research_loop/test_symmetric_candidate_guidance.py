"""New frozen guidance is delivered without changing execution or legacy tasks."""
import copy

import pytest

from scidiscovery.research_loop import contracts as c, controller, guidance, native, selection
from test_history_handoff import finish
from execution_fixtures import execution_registry

DOMAIN_SUFFIX=' Use exp run --mechanism LABEL [--script FILE]; exp lineage-status.'


def prepare(tmp_path,advice,*,portfolio=False):
    public=tmp_path/'public';public.mkdir()
    objective=public/'goal.md';objective.write_text('Compare candidate gains, losses and unknowns under the same original goal.')
    root=tmp_path/'instance'
    from scidiscovery.research_loop import search
    controller.prepare(root,objective,public,['never-call-a-model'],model='no-model',reasoning_effort='high',
        max_tokens=100000,max_seconds=600,max_steps=12,role_guidance=advice,search_policy=search.DEFAULT_POLICY,
        portfolio_policy=dict(review_after_cycles=3) if portfolio else None)
    finish(root,1,'H',[],action='experiment',search_choice=dict(scope='One named mechanism',
        commitments=[dict(label='P',minimal_test='A discriminating scientific question remains unresolved',existing_ref=None)],
        focus=dict(index=1,action='screen')))
    return root


def assigned_views(root):
    job=c.build_job(root,2,'W',[1]);worker_view=c.read(c.workspace(root,2)/'science-task.json')
    finish(root,2,'W',[1],stage_intent='decision',mechanism_findings=[dict(mechanism='h001.m1',basis='blocked',
        finding='This guidance fixture has no domain observation or experiment and cannot complete scientific screening.')])
    judge=c.build_job(root,3,'A',[1,2]);judge_view=c.read(c.workspace(root,3)/'science-task.json')
    return job,worker_view,judge,judge_view


@pytest.mark.parametrize('domain',[False,True])
def test_actual_prepared_worker_and_a_views_deliver_current_advice_and_exact_domain_suffix(tmp_path,domain):
    supplied=dict(guidance.ADVISORY_WORKER_FIRST)
    if domain:supplied['W']+=DOMAIN_SUFFIX
    root=prepare(tmp_path,supplied)
    worker_job,worker_view,judge_job,judge_view=assigned_views(root)
    assert c.settings(root)['role_guidance']==supplied
    assert sum(len(text.encode()) for text in supplied.values())<=guidance.MAX_BYTES==2560
    for role,job,view in [('W',worker_job,worker_view),('A',judge_job,judge_view)]:
        assert job['research_strategy_version']==3 and job['advisory_scope_version']==1
        literal=supplied[role]
        assert job['native_role_instruction']==literal
        assert 'role_guidance' not in view and 'role_instruction' not in view
        prompt,instruction,_=native.render_scientific_request(job,view,{},
            backend_name='codex',instruction_transport='prompt')
        assert prompt.count(literal)==1 and instruction.startswith(literal+'\n')
    assert 'review=A advice' in worker_view['stage_response']
    assert 'decision=pre-delivery Final A' in worker_view['stage_response']
    assert 'Return continue_w with decision_request=null' in judge_view['role_contract']
    assert 'delivery_selection' not in judge_view
    if domain:
        assert worker_job['native_role_instruction'].endswith(DOMAIN_SUFFIX)
        assert DOMAIN_SUFFIX not in judge_job['native_role_instruction']


@pytest.mark.parametrize('custom',[False,True])
def test_no_guidance_fallback_and_custom_frozen_text_preserve_their_actual_assignment(tmp_path,custom):
    advice={role:'Custom scientific instruction for '+role for role in 'HWAE'} if custom else None
    root=prepare(tmp_path,advice)
    w,w_view,judge,a_view=assigned_views(root)
    for role,job,view in [('W',w,w_view),('A',judge,a_view)]:
        expected=advice[role] if custom else guidance.ADVISORY_WORKER_FIRST[role]
        assert job['native_role_instruction']==expected
        assert 'role_guidance' not in view and 'role_instruction' not in view
        prompt,instruction,_=native.render_scientific_request(job,view,{},
            backend_name='codex',instruction_transport='prompt')
        assert prompt.startswith(expected+'\n') and instruction.startswith(expected+'\n')


def test_previously_frozen_native_literal_is_not_rewritten_by_new_defaults(tmp_path,monkeypatch):
    root=prepare(tmp_path,None)
    monkeypatch.setitem(guidance.ADVISORY_WORKER_FIRST,'W','Previously frozen Worker scientific instruction.')
    job=c.build_job(root,2,'W',[1])
    finish(root,2,'W',[1],stage_intent='decision',mechanism_findings=[dict(
        mechanism='h001.m1',basis='blocked',finding='A fixture observation remains missing; no experiment was run.')])
    files=[root/'settings.json',root/'jobs/002.json',c.workspace(root,2)/'science-task.json',c.host_record(c.workspace(root,2))/'result-identity.json']
    before={path:path.read_bytes() for path in files}
    monkeypatch.setitem(guidance.ADVISORY_WORKER_FIRST,'W','New defaults cannot retroactively change an accepted assignment.')
    assert c.build_job(root,2,'W',[1])==job
    c.validate(c.workspace(root,2))
    assert before=={path:path.read_bytes() for path in files}


@pytest.mark.parametrize('recommended',['old','new'])
def test_recommendation_does_not_require_proxy_lead_or_proven_target_optimality(recommended):
    candidates=[dict(id=cid,label=cid,evaluation_key='same-public-evaluation',metric=metric,direction='max',status='ok')
        for cid,metric in [('old',.9),('new',.8)]]
    records=[dict(step=1,role='W',recommendation=None,coverage='complete',candidates=candidates,
        current_candidate=dict(candidate_id='old'),actual_submission=None),
        dict(step=2,role='A',recommendation=recommended,coverage=None,candidates=[],current_candidate=None,actual_submission=None)]
    original=copy.deepcopy(records)
    view=selection.project(records)
    assert view['a_recommended']['candidate_id']==recommended
    assert 'target_best' not in view and records==original
    assert [row['id'] for row in view['candidate_facts']]==['old','new']
