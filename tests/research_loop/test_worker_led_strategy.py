"""Frozen worker-led A decisions and FINAL authorization; no scientific runs."""
import copy

import pytest

from scidiscovery.research_loop import (
    accepted_budget, budget, contracts as c, controller, decision, delivery,
    fork_runtime, guidance, handoff_state, native, research_config, supervision,
)
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_decision_contract import REQUEST
from test_direction_supervision_routing import facts, route
from test_history_handoff import finish, fixture_final_coverage, fixture_portfolio_review, selection_explanation


DEFAULT_STRATEGY = object()


def instance(tmp_path, *, strategy=1, existing=None, guidance_value=None, validate_only=False):
    public = tmp_path / 'public'
    public.mkdir(parents=True, exist_ok=True)
    objective = public / 'goal.md'
    objective.write_text('Compare the exact retained observations and preserve unmet objectives.')
    root = tmp_path / 'instance'
    options = {} if guidance_value is None else dict(role_guidance=guidance_value)
    if strategy is not DEFAULT_STRATEGY:
        options['research_strategy_version'] = strategy
    settings = controller.prepare(root, objective, public, ['never-launched'], max_tokens=100_000,
        max_seconds=3600, max_steps=16, model='fixture', reasoning_effort='high',
        search_policy=None, _existing_settings=existing,
        node_budget_policy={role: dict(max_observed_tokens=1000, max_seconds=60) for role in 'HWAE'},
        validate_only=validate_only, **options)
    return settings if validate_only else root


def publish(root, job, *, action='continue_w', request=None, science='The retained depth remains productive.',
            supervision_result=None, omit_request=False, candidate=None):
    record = c.host_record(c.workspace(root, job['step']))
    payload = dict(summary='Bounded assessment of existing originals', scientific_result=science,
        limitations=['Deterministic fixture; scientific qualification remains unasserted.'],
        implementation_selection=None, search_review=None, progress_assessment=None,
        candidate_recommendation=candidate,
        selection_explanation=selection_explanation() if candidate else None,
        decision_request=request, portfolio_review=None,
        next_action=action, route_reason='Retain the original unfinished W question.')
    if omit_request:
        payload.pop('decision_request')
    if job.get('supervision_request') is not None:
        payload['supervision_result'] = supervision_result
    fixture_portfolio_review(root, job, payload)
    fixture_final_coverage(root, job, payload)
    intent = dict(step=job['step'], task_ref=c.record_ref(root / 'jobs' / f'{job["step"]:03d}.json'),
                  budget_slice=budget.accept_node(root, job))
    c.write(record / 'native-intent.json', intent)
    accepted_budget.accept_native(root, job['step'], record, intent)
    meter = dict(input_tokens=7, cached_input_tokens=2, output_tokens=3)
    observation = dict(coverage='terminal', source='turn.completed', tokens=meter)
    c.write(record / 'science-output.json', payload)
    c.write(record / 'usage.json', meter)
    c.write(record / 'usage-observation.json', observation)
    outcome = dict(returncode=0, stop_reason=None, native_started=False,
        cleanup='observed_group_cleaned', metering=observation, usage=native.usage(root),
        raw_ref=c.file_ref(record / 'science-output.json'))
    accepted = budget.final_a_return(root, job, intent)
    if accepted is not None:
        outcome['final_a_completion'] = budget.final_a_receipt(accepted, intent, meter, outcome['usage'])
    c.write(record / 'native-outcome.json', outcome)
    c.seal_result(root, job['step'])
    return payload


def chain(root):
    finish(root, 1, 'H', [], action='experiment')
    finish(root, 2, 'W', [1], text='A retained controlled observation with unfinished depth.')


def test_new_defaults_freeze_worker_led_guidance_and_jobs(tmp_path):
    root = instance(tmp_path,strategy=DEFAULT_STRATEGY)
    settings = c.settings(root)
    assert settings['research_strategy_version'] == 3
    assert settings['role_guidance'] == guidance.WORKER_FIRST
    chain(root)
    job = c.build_job(root, 3, 'A', [1, 2])
    original = (root / 'jobs/003.json').read_bytes()
    assert job['research_strategy_version'] == 3
    assert c.build_job(root, 3, 'A', [1, 2]) == job
    assert (root / 'jobs/003.json').read_bytes() == original


@pytest.mark.parametrize('version', [0, 4, True, '1'])
def test_only_frozen_supported_strategy_versions_are_supported(tmp_path, version):
    with pytest.raises(ValueError, match='research strategy'):
        instance(tmp_path, strategy=version)


@pytest.mark.parametrize('version', [None, 1, 2])
def test_existing_settings_recreation_and_updates_do_not_upgrade_strategy(tmp_path, version):
    root = instance(tmp_path / 'source', strategy=version, guidance_value=guidance.DEFAULT)
    original = c.settings(root)
    child_settings = instance(tmp_path / 'recreated', existing=original, validate_only=True)
    assert child_settings.get('research_strategy_version') == version
    assert ('research_strategy_version' in child_settings) == ('research_strategy_version' in original)
    assert child_settings['role_guidance'] == original['role_guidance'] == guidance.DEFAULT
    document = research_config.to_document(root)
    document['agent']['model'] = 'other-fixture'
    updated = research_config.validate_update(root, document)
    assert updated.get('research_strategy_version') == version
    assert ('research_strategy_version' in updated) == ('research_strategy_version' in original)
    assert updated['role_guidance'] == original['role_guidance']
    assert fork_runtime.changed_task_settings(original, dict(original, research_strategy_version=3)) == ['research_strategy_version']


def test_ordinary_a_can_continue_existing_work_with_null_request_and_no_repeat_comparison(tmp_path, monkeypatch):
    root = instance(tmp_path)
    chain(root)
    job = c.build_job(root, 3, 'A', [1, 2])
    monkeypatch.setattr(decision, '_requests', lambda *args, **kwargs:
        pytest.fail('Continuing existing work is not a new evidence request'))
    payload = publish(root, job)
    assert c.read(c.host_record(c.workspace(root, 3)) / 'result-identity.json')['control_outcome']['decision_status'] == 'applied'
    selected = controller._route(root, 3, 'A', payload, job, 1, 2)
    assert selected[0] == 'W' and selected[1] == [1, 2, 3]
    assert 'original question' in selected[2] and 'unfinished work' in selected[2]


@pytest.mark.parametrize('changed', [dict(omit_request=True), dict(request={}),
    dict(request=dict(decision='mechanism', question='Gap?', change_criterion='  ')),
    dict(science='  '), dict(action='revise_h'), dict(action='extract')])
def test_free_continuation_does_not_accept_missing_partial_or_blank_science(tmp_path, changed):
    root = instance(tmp_path)
    chain(root)
    job = c.build_job(root, 3, 'A', [1, 2])
    with pytest.raises(OutputRepairRequired):
        publish(root, job, **changed)
    assert not (c.host_record(c.workspace(root, 3)) / 'result-identity.json').exists()
    assert not (root / 'jobs/004.json').exists()


def test_explicit_completed_request_guard_still_applies_without_optional_old_contract(tmp_path):
    root = instance(tmp_path)
    chain(root)
    assert c.settings(root).get('decision_contract_version') is None
    publish(root, c.build_job(root, 3, 'A', [1, 2]), request=copy.deepcopy(REQUEST))
    finish(root, 4, 'W', [1, 2, 3], text='A completed response exists, but is absent from the later assessment cut.')
    job = c.build_job(root, 5, 'A', [1, 2, 3])
    publish(root, job, request=copy.deepcopy(REQUEST))
    seal = c.read(c.host_record(c.workspace(root, 5)) / 'result-identity.json')
    assert seal['control_outcome']['decision_status'] == 'unresolved'
    assert any(row['code'] == 'a_decision_request_repeated' for row in seal['control_outcome']['diagnostics'])


def test_forced_reflection_can_endorse_depth_and_preserves_pending_question(tmp_path):
    selected, frozen = controller._supervision_route(2,
        dict(role='W', stage_intent='handoff', route_reason='Is deeper controlled work still productive?'),
        route(), facts=facts(), source_job=dict(role='W', research_strategy_version=1))
    assert selected[0] == 'A' and frozen['automatic']
    assert 'do not establish stagnation' in selected[2]
    assert 'Is deeper controlled work still productive?' in selected[2]
    root = instance(tmp_path)
    chain(root)
    # Bind an exact current cut while retaining the same automatic-review scope.
    frozen = supervision.request(supervision.empty(), automatic=True)
    job = c.build_job(root, 3, 'A', [1, 2], supervision_request=frozen)
    publish(root, job, supervision_result='continue_existing')
    assert c.read(c.host_record(c.workspace(root, 3)) / 'result-identity.json')['control_outcome']['decision_status'] == 'applied'


def test_explicit_supervision_evidence_request_cannot_use_null(tmp_path):
    root = instance(tmp_path)
    chain(root)
    frozen = supervision.request(supervision.empty(), automatic=True)
    job = c.build_job(root, 3, 'A', [1, 2], supervision_request=frozen)
    with pytest.raises(OutputRepairRequired, match='supervision'):
        publish(root, job, supervision_result='request_evidence')


@pytest.mark.parametrize('action', ['finish', 'deliver'])
def test_ordinary_terminal_choice_enters_real_final_before_any_effect(tmp_path, monkeypatch, action):
    root = instance(tmp_path)
    chain(root)
    job = c.build_job(root, 3, 'A', [1, 2])
    publish(root, job, action=action)
    original = (c.host_record(c.workspace(root, 3)) / 'result-identity.json').read_bytes()
    called = []
    def final_call(root, step, **kwargs):
        final_job = c.read_job(root / 'jobs' / f'{step:03d}.json')
        assert step == 4 and final_job['final_only']
        assert budget.closeout_state(root)['begin']['mode'] == 'final'
        assert not budget.closeout_state(root)['actions']
        called.append(step)
        publish(root, final_job, action='finish', science='Completed assessment; unfinished observations remain explicit.')
    monkeypatch.setattr(controller, '_native_call', final_call)
    monkeypatch.setattr(delivery, 'invoke', lambda *args, **kwargs: pytest.fail('No adapter effect was authorized'))
    result = controller.run(root)
    assert result['status'] == 'final_a_completed' and called == [4], result
    assert budget.final_a_barrier(root)['step'] == 4
    assert 'authorization_step' not in budget.closeout_state(root)['begin']
    assert (c.host_record(c.workspace(root, 3)) / 'result-identity.json').read_bytes() == original
    assert controller.run(root)['status'] == 'final_a_completed' and called == [4]


@pytest.mark.parametrize('saved_route', [False, True])
def test_worker_decision_enters_one_final_directly_on_run_and_cold_resume(tmp_path, monkeypatch, saved_route):
    root = instance(tmp_path)
    chain(root)
    if saved_route:
        controller._save_route(root, 2, ('A', [1, 2], 'An accepted decision request', None, None, None),
            1, 2, resumed=None)
    calls = []
    def final_call(root, step, **kwargs):
        job = c.read_job(root / 'jobs' / f'{step:03d}.json')
        assert step == 3 and job['role'] == 'A' and job['final_only']
        calls.append(step)
        publish(root, job, action='finish')
    monkeypatch.setattr(controller, '_native_call', final_call)
    result = controller.run(root)
    assert result['status'] == 'final_a_completed', result
    assert calls == [3] and budget.closeout_state(root)['begin']['stop_reason'] == 'worker_final_decision'
    assert not (root / 'jobs/004.json').exists()
    assert controller.run(root)['status'] == 'final_a_completed' and calls == [3]


@pytest.mark.parametrize('intent', ['handoff', 'continue', 'review', 'revise_h'])
def test_worker_nonfinal_intents_and_legacy_decision_do_not_request_final(intent):
    job = dict(role='W', research_strategy_version=1)
    assert not controller._worker_led_requests_final(job, dict(stage_intent=intent))
    assert not controller._worker_led_requests_final(dict(role='W'), dict(stage_intent='decision'))


def test_delivery_runner_and_direct_reentry_cannot_bypass_final(tmp_path, monkeypatch):
    job = dict(step=3, role='A', research_strategy_version=1)
    payload = dict(next_action='deliver')
    called = []
    monkeypatch.setattr(controller, '_start_budget_closeout', lambda *args:
        called.append(args[1:]) or dict(status='final_requested'))
    monkeypatch.setattr(delivery, 'prepare_binding', lambda *args: pytest.fail('Ordinary A cannot prepare an adapter effect'))
    assert controller._run_delivery_actions(tmp_path, job, payload)['status'] == 'final_requested'
    monkeypatch.setattr(c, 'read_job', lambda *args: job)
    monkeypatch.setattr(handoff_state, 'read_sealed', lambda *args: payload)
    assert controller._run_direct_delivery(tmp_path,
        dict(source_cutoff=3, authorization_step=3, first_step=4))['status'] == 'final_requested'
    assert len(called) == 2


def test_actual_action_admission_rejects_marked_ordinary_a(tmp_path, monkeypatch):
    from scidiscovery.research_loop import configuration_updates, protocol
    policy = dict(research_strategy_version=1, delivery_policy=dict(adapter={'fixture': True}))
    monkeypatch.setattr(protocol, 'require_current', lambda *args: None)
    monkeypatch.setattr(configuration_updates, 'for_job', lambda *args: policy)
    monkeypatch.setattr(budget, 'closeout_state', lambda *args: dict(begin=dict(mode='model')))
    monkeypatch.setattr(budget, 'execution_blocked', lambda *args, **kwargs: False)
    monkeypatch.setattr(native, 'usage', lambda *args: {})
    monkeypatch.setattr(budget, 'snapshot', lambda *args: {})
    monkeypatch.setattr(budget, 'require_final_a_delivery_eligibility', lambda *args: None)
    c.write(tmp_path / 'jobs/003.json', dict(step=3, role='A', research_strategy_version=1))
    with pytest.raises(budget.NodeBudgetUnavailable, match='final_author_required'):
        budget.accept_action(tmp_path, 'submit', 'fixture', dict(step=3), {}, 1)


@pytest.mark.process_e2e
def test_final_a_authorized_delivery_is_allowed(tmp_path, monkeypatch):
    from test_closeout_protocol import scene
    from unittest.mock import patch
    prepare = controller.prepare
    with patch.object(controller,'prepare',lambda *args,**kwargs: prepare(*args,**kwargs,research_strategy_version=1)):
        root = scene(tmp_path, [])
    publish(root, c.build_job(root, 3, 'A', [1, 2]), action='deliver', candidate='run-1')
    final_jobs = []
    def final_call(root, step, **kwargs):
        job = c.read_job(root / 'jobs' / f'{step:03d}.json')
        assert job['final_only'] and step == 4
        assert not budget.closeout_state(root)['actions']
        final_jobs.append(job)
        publish(root, job, action='deliver', candidate='run-1')
    monkeypatch.setattr(controller, '_native_call', final_call)
    assert controller.run(root)['status'] == 'delivery_confirmed'
    assert len(final_jobs) == 1
    actions = budget.closeout_state(root)['actions']
    assert [row['intent']['action_kind'] for row in actions] == ['submit', 'confirm']
    assert all(row['intent']['decision']['step'] == 4 and row['intent'].get('final_return') for row in actions)
    assert controller.run(root)['status'] == 'delivery_confirmed' and len(final_jobs) == 1
