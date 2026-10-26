"""Reopen control regression with the qualified Local process namespace.

Git snapshots, accepted calls and seals are real control originals. This suite
uses inert Python processes and authored synthetic science; no model runs.
"""
import copy

import pytest

from scidiscovery.research_loop import breadth, contracts as c, execution, native, search
from execution_fixtures import execution_registry
from test_breadth_science import findings, h, prepare, run, worker as configure_worker
from test_history_handoff import finish, fixture_portfolio_review
from test_search import assess, focus, plan, review, worker


pytestmark = pytest.mark.process_e2e


def screened(tmp_path, *, labels=('P',)):
    root = prepare(tmp_path, qualified_local=True, portfolio=True)
    h(root, labels=labels)
    worker(root, 2, [1], observed=['h001.m1'], qualified_local=True)
    assess(root, 3, [1, 2], action='finish', search_choice=review())
    return root


def reopen_choice(reason='A newly available observation leaves the original discriminator unanswered.'):
    return dict(dispositions=[dict(mechanism_ref='h001.m1', status='pending', reason=reason)],
        next_focus=focus(None, 'screen'))


def test_a_reopens_screened_mechanism_into_unfinished_plan(tmp_path, execution_registry):
    root = screened(tmp_path)
    before = search.project(root, 4)
    assert before['order']['remaining'] == []
    original = (execution.record(root, 3) / 'science-output.json').read_bytes()
    choice = reopen_choice()
    assess(root, 4, [1, 3], search_choice=choice)

    state = search.project(root, 5)
    expected = dict(mechanism_ref='h001.m1', planned_step=4)
    assert state['order']['remaining'] == [expected]
    assert state['order']['allocation'] == expected
    assert state['focus'] == focus('h001.m1', 'screen')
    assert state['mechanisms']['h001.m1']['disposition'] == dict(
        reason=choice['dispositions'][0]['reason'], source_a=4)
    assert state['mechanisms']['h001.m1']['source_h'] == 1
    assert state['opportunity_counts'] == before['opportunity_counts'] == {'h001.m1': 1}
    assert state['portfolio']['invested'] == before['portfolio']['invested'] == {'h001.m1': 1}
    assert state['order']['seen_w'] == before['order']['seen_w']
    job = c.build_job(root, 5, 'W', [1, 4])
    assert job['search']['allocation'] == expected
    assert job['search']['coverage_obligation'][0]['mechanism_ref'] == 'h001.m1'
    assert search.launch_reason(root, job) is None
    assert (execution.record(root, 3) / 'science-output.json').read_bytes() == original


def test_repeated_pending_preserves_original_disposition_and_single_queue_entry(tmp_path, execution_registry):
    root = screened(tmp_path)
    assess(root, 4, [1, 3], search_choice=reopen_choice())
    before = search.project(root, 5)
    original = (execution.record(root, 4) / 'science-output.json').read_bytes()
    job = c.build_job(root, 5, 'A', [1, 4])
    payload = dict(next_action='finish', route_reason='Retain the unanswered discriminator',
        search_review=reopen_choice('A restatement must not replace the adopted reopening reason.'))
    interpreted = search.interpret(root, job, payload, state=before)
    assert interpreted['diagnostics'] == []
    assert interpreted['state']['order']['remaining'] == before['order']['remaining']
    assert interpreted['state']['mechanisms'] == before['mechanisms']

    assess(root, 5, [1, 4], action='finish', search_choice=payload['search_review'])
    after = search.project(root, 6)
    assert after['order']['remaining'] == [dict(mechanism_ref='h001.m1', planned_step=4)]
    assert after['mechanisms'] == before['mechanisms']
    assert after['opportunity_counts'] == before['opportunity_counts']
    assert after['portfolio']['invested'] == before['portfolio']['invested']
    assert (execution.record(root, 4) / 'science-output.json').read_bytes() == original


def test_reopen_does_not_refund_initial_calls_or_renew_used_grant(tmp_path, execution_registry):
    root = prepare(tmp_path, qualified_local=True, portfolio=True)
    h(root, labels=('P', 'Q'))
    worker(root, 2, [1], observed=['h001.m1'], qualified_local=True)
    assess(root, 3, [1, 2], search_choice=dict(dispositions=[], next_focus=focus('h001.m1', 'screen')))
    _, granted = configure_worker(root, tmp_path, 4, [1, 2, 3], qualified_local=True)
    accepted = run(root, granted, 'P', qualified_local=True)
    assert accepted['mechanism_binding']['grant']['source_a'] == 3
    with pytest.raises(breadth.OpportunityRequired):
        run(root, granted, 'P', qualified_local=True)
    finish(root, 4, 'W', [1, 2, 3], mechanism_findings=findings('P', blocked=('Q',)),
        preserve_native_intent=True)
    assess(root, 5, [1, 4], action='finish', search_choice=review())
    before = search.project(root, 6)
    assess(root, 6, [1, 5], search_choice=reopen_choice())
    after = search.project(root, 7)
    assert after['opportunity_counts'] == before['opportunity_counts'] == {'h001.m1': 2}
    assert after['opportunity_grants'] == before['opportunity_grants']
    assert after['portfolio']['invested'] == before['portfolio']['invested'] == {'h001.m1': 2}
    assert after['extra_grant'] == before['extra_grant'] == dict(mechanism_ref='h001.m1', source_a=3)
    _, config = configure_worker(root, tmp_path, 7, [1, 6], qualified_local=True)
    assert config['breadth']['initial_calls'] == 1
    assert {row['mechanism_ref']: row['accepted_count'] for row in config['breadth']['baseline']} == {
        'h001.m1': 2, 'h001.m2': 0}
    assert config['breadth']['grant']['extra_calls'] == config['breadth']['grant']['used_calls'] == 1
    with pytest.raises(breadth.OpportunityRequired):
        run(root, config, 'P', qualified_local=True)
    run(root, config, 'Q', qualified_local=True)
    # Once all initial opportunities are consumed, the one-call exception no
    # longer limits ordinary calls; reopening does not introduce a W quota.
    assert run(root, config, 'P', qualified_local=True)['status'] == 'known_success'


def test_h_existing_ref_reopen_preserves_calls_and_investment(tmp_path, execution_registry):
    root = screened(tmp_path)
    before = copy.deepcopy(search.project(root, 4))
    h(root, 4, [1, 3], labels=('Revised P', 'New Q'), existing='h001.m1')
    state = search.project(root, 5)
    assert state['opportunity_counts'] == before['opportunity_counts'] == {'h001.m1': 1}
    assert state['portfolio']['invested'] == before['portfolio']['invested'] == {'h001.m1': 1}
    assert state['mechanisms']['h001.m1']['original_h'] == 1
    assert state['mechanisms']['h001.m1']['source_h'] == 4
    assert state['mechanisms']['h001.m1']['status'] == 'pending'
    assert 'disposition' not in state['mechanisms']['h001.m1']
    assert state['authorized'] == {'h001.m1', 'h004.m2'}
    assert state['order']['remaining'] == [dict(mechanism_ref=ref, planned_step=4)
        for ref in ('h001.m1', 'h004.m2')]
    job = c.build_job(root, 5, 'W', [4])
    assert [row['mechanism_ref'] for row in job['search']['breadth']['mechanisms']] == ['h001.m1', 'h004.m2']


def test_explicit_a_focus_after_reopen_retains_fresh_exact_grant(tmp_path, execution_registry):
    root = screened(tmp_path, labels=('P', 'Q'))
    choice = reopen_choice()
    choice['next_focus'] = focus('h001.m1', 'screen')
    assess(root, 4, [1, 3], search_choice=choice)
    state = search.project(root, 5)
    assert state['opportunity_counts'] == {'h001.m1': 1}
    assert state['extra_grant'] == dict(mechanism_ref='h001.m1', source_a=4)
    _, config = configure_worker(root, tmp_path, 5, [1, 4], qualified_local=True)
    assert config['breadth']['grant']['source_a'] == 4
    assert config['breadth']['grant']['used_calls'] == 0
    assert run(root, config, 'P', qualified_local=True)['mechanism_binding']['grant']['source_a'] == 4
    with pytest.raises(breadth.OpportunityRequired):
        run(root, config, 'P', qualified_local=True)


def test_screened_without_reopen_stays_visible_outside_unfinished_plan(tmp_path, execution_registry):
    root = screened(tmp_path, labels=('P', 'Q'))
    state = search.project(root, 4)
    assert state['mechanisms']['h001.m1']['status'] == 'screened'
    assert state['order']['remaining'] == [dict(mechanism_ref='h001.m2', planned_step=1)]
    assert search.portfolio_view(state, 3)['unexcluded_count'] == 2
    assess(root, 4, [1, 3], search_choice=dict(dispositions=[], next_focus=focus(None, 'screen')))
    job = c.build_job(root, 5, 'W', [1, 4])
    assert job['search']['focus'] == focus('h001.m2', 'screen')
    assert {row['mechanism_ref']: row['status'] for row in job['search']['breadth']['mechanisms']} == {
        'h001.m1': 'screened', 'h001.m2': 'pending'}
    assert [row['mechanism_ref'] for row in job['search']['coverage_obligation']] == ['h001.m2']


def extract_assessment(root, status):
    """A real sealed extraction request; its science is explicitly synthetic."""
    job = c.build_job(root, 3, 'A', [1, 2])
    record = execution.record(root, 3)
    payload = dict(summary='Synthetic request for discriminating clarification',
        scientific_result='Synthetic fixture retains the unanswered discriminator; no scientific qualification.',
        limitations=['Synthetic control fixture; no scientific qualification'], implementation_selection=None,
        candidate_recommendation=None, progress_assessment=None, portfolio_review=None,
        search_review=review(status=status), next_action='extract',
        route_reason='Ask E to clarify the missing discriminating input before allocating another W.',
        decision_request=dict(decision='mechanism', question='Which discriminating input remains unavailable?',
            change_criterion='An available discriminating input would change the synthetic mechanism decision.'))
    fixture_portfolio_review(root, job, payload)
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/003.json')))
    c.write(record / 'science-output.json', payload)
    c.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None, native_started=False, usage=native.usage(root),
        raw_ref=c.file_ref(record / 'science-output.json')))
    c.seal_result(root, 3)
    control = c.read(record / 'result-identity.json')['control_outcome']
    assert control['search_status'] == control['decision_status'] == 'applied'


@pytest.mark.parametrize('prior_status', ['deferred', 'pending'])
@pytest.mark.parametrize('action', ['continue_w', 'finish'])
def test_pending_survives_intermediate_extract_assessment(tmp_path, execution_registry, prior_status, action):
    root = prepare(tmp_path, qualified_local=True, portfolio=True)
    finish(root, 1, 'H', [], action='experiment', search_choice=plan(action='improve'))
    job, _, _ = worker(root, 2, [1], qualified_local=True)
    old = dict(mechanism_ref='h001.m1', planned_step=1)
    assert job['search']['focus'] == focus('h001.m1', 'improve')
    assert job['search']['allocation'] == old
    extract_assessment(root, prior_status)
    awaiting = search.project(root, 4)
    assert awaiting['order']['remaining'] == [old]
    assert awaiting['order']['seen_w'] == set()

    c.build_job(root, 4, 'E', [3], evidence_return=dict(step=3, role='A'))
    finish(root, 4, 'E', [3], text='Synthetic E clarification retains the unanswered discriminator; no scientific qualification.')
    before = search.project(root, 5)
    assert before['portfolio']['invested'] == {}
    assert before['opportunity_counts'] == {'h001.m1': 1}
    original_w = (execution.record(root, 2) / 'science-output.json').read_bytes()
    choice = reopen_choice('Synthetic E clarification leaves a concrete discriminator unanswered; reopen it without replaying prior calls.')
    assess(root, 5, [1, 2, 3, 4], action=action, search_choice=choice)

    state = search.project(root, 6)
    assert state['order']['remaining'] == [old]
    assert state['mechanisms']['h001.m1']['status'] == 'pending'
    if prior_status == 'deferred':
        assert state['mechanisms']['h001.m1']['disposition'] == dict(
            reason=choice['dispositions'][0]['reason'], source_a=5)
    else:
        assert 'disposition' not in state['mechanisms']['h001.m1']
    assert state['opportunity_counts'] == before['opportunity_counts']
    assert state['opportunity_grants'] == before['opportunity_grants'] == {}
    assert state['extra_grant'] == before['extra_grant'] is None
    assert state['portfolio']['invested'] == {'h001.m1': 1}
    assert len(state['portfolio']['seen_w']) == len(state['order']['seen_w']) == 1
    assert len(state['accepted_slices']) == len(before['accepted_slices']) == 1
    if action == 'continue_w':
        assert state['order']['allocation'] == old
        selected = c.build_job(root, 6, 'W', [1, 5])
        assert selected['search']['allocation'] == old
        assert selected['search']['breadth']['baseline'] == [dict(mechanism_ref='h001.m1', accepted_count=1)]
        assert selected['search']['breadth']['grant'] is None
        assert search.launch_reason(root, selected) is None
    assert (execution.record(root, 2) / 'science-output.json').read_bytes() == original_w
