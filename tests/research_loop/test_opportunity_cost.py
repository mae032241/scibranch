"""Unserved commitments need a scientific tradeoff, not host scientific ranking."""

import pytest

from scidiscovery.research_loop import contracts as c, controller, guidance, search
from test_history_handoff import finish
from execution_fixtures import execution_registry
from test_portfolio_review import P, Q, R

S = 'h001.m4'


def test_screened_routes_are_counted_but_waived_routes_are_excluded():
    state=dict(mechanisms={P:dict(status='screened'),Q:dict(status='pending'),R:dict(status='waived')},
        portfolio=dict(seen_w={(2,'first-w'),(3,'second-w'),(4,'third-w')},invested={Q:2},deferred={P:3,R:4},last_branch=Q,last_branches=[Q],pending_w=None,last_decision_step=5))
    view=search.portfolio_view(state,3)
    assert view['completed_cycles']==3 and view['overdue_count_if_assessed']==1
    assert view['review_reminder'] is False  # Counts expose delay without requesting intervention.
    assert view['unexcluded_count']==2
    state['mechanisms'][P]['status']='waived'
    excluded=search.portfolio_view(state,3)
    assert excluded['review_reminder'] is False and excluded['overdue_count_if_assessed']==0
    assert search.portfolio_view(state,None)['review_after_cycles'] is None


@pytest.mark.parametrize('custom', [False, True])
def test_new_real_task_guidance_is_frozen_domain_suffix_survives_and_custom_text_is_not_rewritten(tmp_path, custom):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Original scientific goal and mandatory independent checks')
    domain = ' Configured experiment tools select a new plan or exact parent; query uncertain calls before further work.'
    advice = dict(guidance.ADVISORY_WORKER_FIRST)
    if custom:
        advice = {role: f'Custom scientific permissions for {role}; preserve original constraints.' for role in advice}
    advice['W'] += domain
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-launched'], max_tokens=100_000, max_seconds=3600,
                       max_steps=16, model='fixture', reasoning_effort='high', role_guidance=advice,
                       portfolio_policy=dict(review_after_cycles=3))
    plan = dict(scope='Prioritized falsifiable solutions', commitments=[
        dict(label='P', minimal_test='Cheap decision-changing comparison', existing_ref=None)],
        focus=dict(index=1, action='screen'))
    finish(root, 1, 'H', [], action='experiment', search_choice=plan)
    job = c.build_job(root, 2, 'W', [1], instruction='Exact scientific question')
    actual = c.read(c.workspace(root, 2) / 'science-task.json')
    assert job['native_role_instruction'] == advice['W']
    assert 'role_guidance' not in actual
    assert job['native_role_instruction'].endswith(domain)
    assert sum(len(value.encode()) for value in advice.values()) <= guidance.MAX_BYTES
    assert c.settings(root)['role_guidance'] == advice
    assert c.build_job(root, 2, 'W', [1], instruction='Exact scientific question') == job
