"""Unserved commitments need a scientific tradeoff, not host scientific ranking."""
import copy
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c, controller, guidance, history, native, search, decision
from test_history_handoff import finish
from execution_fixtures import execution_registry
from scidiscovery.research_loop.protocol import CURRENT, UnsupportedProtocol
from test_portfolio_review import P, Q, R, REQUEST, TRADEOFF, author, pending_three, prepare, route_instruction, worker

S = 'h001.m4'


def three_branches(tmp_path):
    root = prepare(tmp_path, threshold=3)
    pending_three(root)
    return root


@pytest.mark.parametrize('branch,review',[(None,None),(P,None),(S,None),(None,TRADEOFF)])
def test_pending_alternatives_remain_visible_without_report_gate_or_investment_reset(tmp_path,branch,review):
    root=three_branches(tmp_path)
    job,payload=author(root,7,[1,6],branch=branch,portfolio=review)
    assert decision.reason(root,job,payload) is None
    receipt=c.read(c.host_record(c.workspace(root,7))/'result-identity.json')['control_outcome']
    assert 'portfolio_status' not in receipt
    state=search.project(root,8)['portfolio']
    assert len(state['seen_w'])==3 and state['pending_w'] is None
    assert state['invested']=={P:1,Q:1,R:1} and state['deferred'][S]==3
    assert c.science_view(job,root)['lineage']['mechanisms']['portfolio']['review_reminder'] is True


def test_screened_routes_are_counted_but_waived_routes_are_excluded():
    state=dict(mechanisms={P:dict(status='screened'),Q:dict(status='pending'),R:dict(status='waived')},
        portfolio=dict(invested={Q:2},deferred={P:3,R:4},last_branch=Q,last_branches=[Q],pending_w=None,last_decision_step=5))
    view=search.portfolio_view(state,3)
    assert view['review_reminder'] is True and view['overdue_count_if_assessed']==1
    assert view['unexcluded_count']==2
    state['mechanisms'][P]['status']='waived'
    assert search.portfolio_view(state,3)['review_reminder'] is False
    assert search.portfolio_view(state,None)['review_after_cycles'] is None


def test_explicit_review_retains_investment_and_does_not_recount_w(tmp_path):
    root=three_branches(tmp_path)
    job,payload=author(root,7,[1,6],branch=R,portfolio=TRADEOFF)
    state=search.project(root,8)['portfolio']
    assert len(state['seen_w'])==3 and state['pending_w'] is None
    assert state['invested']=={P:1,Q:1,R:1} and state['deferred'][S]==3
    assert state['last_decision_step']==7
    original=(c.host_record(c.workspace(root,7))/'science-output.json').read_bytes()
    assert c.build_job(root,7,'A',[1,6],instruction=job['instruction'])==job
    assert search.project(root,8)['portfolio']==state
    assert (c.host_record(c.workspace(root,7))/'science-output.json').read_bytes()==original


@pytest.mark.parametrize('action', ['audit', 'extract'])
def test_unsupported_audit_feedback_or_e_return_does_not_reset_delay_or_count_w_twice(tmp_path, monkeypatch, action):
    root = three_branches(tmp_path)
    author(root, 7, [1, 6], branch=R, action=action,
           request=REQUEST if action == 'extract' else None, portfolio=TRADEOFF)
    before = search.project(root, 8)['portfolio']
    assert len(before['seen_w']) == 2 and before['pending_w']['step'] == 6
    assert before['deferred'][S] == 2 and before['last_decision_step']==5
    accepted = []

    def execute(root, step):
        job = c.read(root / 'jobs' / f'{step:03d}.json')
        parents = [int(Path(ref['path']).parts[2]) for ref in job['direct_refs']]
        accepted.append(job['role'])
        if job['role'] == 'E':
            finish(root, step, 'E', parents, text='An independently requested bounded finding')
        else:
            assert job['role'] == 'A' and 6 in parents
            c.write(root / 'pause.request', dict(reason='Stop after assessing the original W'))
            author(root, step, parents, action='revise_h')

    monkeypatch.setattr(native, 'execute_native', execute)
    assert controller.run(root)['status'] == 'paused'
    assert accepted == (['A'] if action == 'audit' else ['E', 'A'])
    cutoff = max(int(path.stem) for path in (root / 'jobs').glob('*.json')) + 1
    after = search.project(root, cutoff)['portfolio']
    assert len(after['seen_w']) == 3 and after['pending_w'] is None
    assert after['deferred'][S] == 3 and after['last_decision_step']==cutoff-1
    assert controller.run(root)['status'] == 'paused'
    assert len(search.project(root, cutoff)['portfolio']['seen_w']) == 3


def test_h_revision_rename_and_omission_preserve_unserved_commitment(tmp_path):
    root = three_branches(tmp_path)
    author(root, 7, [1, 6], action='revise_h', dispositions=[
        dict(mechanism_ref=S, status='deferred', reason='Same scientific deferral remains unresolved')])
    assert search.project(root, 8)['portfolio']['deferred'][S] == 3
    c.build_job(root, 8, 'H', [1, 6, 7], instruction='Exact scientific question')
    finish(root, 8, 'H', [1, 6, 7], action='experiment', search_choice=dict(
        scope='Retain original constraints and explain prioritization',
        commitments=[dict(label='Renamed S', minimal_test='Original discriminating comparison', existing_ref=S),
                     dict(label='New T', minimal_test='A newly declared independent comparison', existing_ref=None)],
        focus=dict(label=R, action='screen')))
    assert search.project(root, 9)['mechanisms'][S]['label'] == 'Renamed S'
    assert search.project(root, 9)['portfolio']['deferred'][S] == 3
    # H retains scientific authority; reminders do not gate A continuation.
    worker(root, 9, [8], observed='h008.m2')
    job, payload = author(root, 10, [8, 9], action='revise_h')
    assert decision.reason(root,job,payload) is None
    state = search.project(root, 11)['portfolio']
    assert state['deferred'][S] == 4 and len(state['seen_w']) == 4
    assert state['invested'] == {P: 1, Q: 1, R: 1, 'h008.m2': 1}


def test_actual_brief_separates_completed_and_pending_and_preserves_full_readonly_index(tmp_path):
    root = three_branches(tmp_path)
    originals = {path:path.read_bytes() for step in range(1,7) for path in (
        root/'jobs'/f'{step:03d}.json',
        c.host_record(c.workspace(root,step))/'science-output.json',
        c.host_record(c.workspace(root,step))/'result-identity.json')}
    job = c.build_job(root, 7, 'A', [1, 6], instruction=route_instruction(root, [1, 6]))
    work = c.workspace(root, 7)
    view = c.read(work / 'science-task.json')
    assert view == c.science_view(job, root)
    brief = view['lineage']
    assert len((json.dumps(brief, ensure_ascii=False, indent=2) + '\n').encode()) <= history.MAX_BRIEF_BYTES
    mechanism_view = brief['mechanisms']
    portfolio = mechanism_view['portfolio']
    assert portfolio['version'] == 2 and portfolio['pending_assessment'] is True
    assert portfolio['review_after_cycles'] == 3 and portfolio['overdue_count_if_assessed'] == 1
    assert portfolio['completed_cycles'] == 0 and portfolio['cycles_if_assessed'] == 1
    assert portfolio['review_reminder'] is True
    # Navigation excerpts may all be omitted to preserve the compact byte bound.
    assert len(mechanism_view['overview']) <= 4
    assert len(mechanism_view['overview']) + mechanism_view['overview_omitted_count'] == 4
    assert [row['mechanism_ref'] for row in mechanism_view['overview']]==[P,Q,R,S][:len(mechanism_view['overview'])]
    page_path = work / mechanism_view['index']
    assert page_path.stat().st_mode & 0o222 == 0
    index = c.read(page_path)
    assert [row['mechanism_ref'] for row in index['mechanisms']] == [P, Q, R, S]
    rows = {row['mechanism_ref']:row for row in index['mechanisms']}
    assert rows[R]['completed_cycles'] == 0
    assert rows[S]['unserved_cycles'] == 2 and rows[S]['unserved_cycles_if_assessed'] == 3
    assert all(path.read_bytes() == raw for path,raw in originals.items())
    page_path.chmod(0o600)
    page_path.write_text('{}\n')
    with pytest.raises(ValueError):
        c._validate_inputs(root, work, job)


def test_version_one_write_is_rejected_and_current_frozen_jobs_remain_idempotent(tmp_path):
    with pytest.raises(ValueError, match='Unsupported frozen portfolio semantics'):
        prepare(tmp_path / 'retired', version=1)
    assert not (tmp_path / 'retired/instance').exists()
    root = three_branches(tmp_path / 'current')
    job, payload = author(root, 7, [1, 6], branch=R)
    assert job['handoff_protocol'] == CURRENT and c.settings(root)['portfolio_policy']['version'] == 2
    assert decision.reason(root,job,payload) is None
    assert 'review_reminder' in c.science_view(job, root)['lineage']['mechanisms']['portfolio']
    paths = [root / 'settings.json', root / 'jobs/007.json', c.workspace(root, 7) / 'science-task.json',
             c.host_record(c.workspace(root, 7)) / 'science-result.schema.json',
             c.host_record(c.workspace(root, 7)) / 'result-identity.json']
    original = {path: path.read_bytes() for path in paths}
    assert c.build_job(root, 7, 'A', [1, 6], instruction=job['instruction']) == job
    c.validate(c.workspace(root, 7))
    assert all(path.read_bytes() == data for path, data in original.items())
    future = c.build_job(root, 8, 'W', [1, 6, 7], instruction=payload['route_reason'])
    assert future['handoff_protocol'] == CURRENT and future['role_guidance'] == guidance.OPPORTUNITY['W']
    with pytest.raises(UnsupportedProtocol):
        c.result_schema('A', 11)


@pytest.mark.parametrize('custom', [False, True])
def test_new_real_task_guidance_is_frozen_domain_suffix_survives_and_custom_text_is_not_rewritten(tmp_path, custom):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Original scientific goal and mandatory independent checks')
    domain = ' Configured experiment tools select a new plan or exact parent; query uncertain calls before further work.'
    advice = dict(guidance.OPPORTUNITY)
    if custom:
        advice = {role: f'Custom scientific permissions for {role}; preserve original constraints.' for role in advice}
    advice['W'] += domain
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-launched'], max_tokens=100_000, max_seconds=3600,
                       max_steps=16, model='fixture', reasoning_effort='high', role_guidance=advice,
                       portfolio_policy=dict(review_after_cycles=3))
    plan = dict(scope='Prioritized falsifiable solutions', commitments=[
        dict(label='P', minimal_test='Cheap decision-changing comparison', existing_ref=None)],
        focus=dict(label='P', action='screen'))
    finish(root, 1, 'H', [], action='experiment', search_choice=plan)
    job = c.build_job(root, 2, 'W', [1], instruction='Exact scientific question')
    actual = c.read(c.workspace(root, 2) / 'science-task.json')
    assert actual['role_guidance'] == job['native_role_instruction'] == advice['W']
    assert actual['role_guidance'].endswith(domain)
    assert sum(len(value.encode()) for value in advice.values()) <= guidance.MAX_BYTES
    assert c.settings(root)['role_guidance'] == advice
    assert c.build_job(root, 2, 'W', [1], instruction='Exact scientific question') == job
