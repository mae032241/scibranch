"""Frozen role token shares route optional thinking by control; no model calls."""
from test_history_handoff import fixture_final_coverage, selection_explanation
import importlib.util
from pathlib import Path

import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, delivery_review, native, role_share, search
from scidiscovery.research_loop.routing import Route

BIG = 10_000_000


@pytest.fixture(autouse=True)
def _stopped_source_seals(request, monkeypatch):
    if request.node.get_closest_marker('process_e2e') is None:
        from test_direct_input_bindings import stopped_seals
        stopped_seals(monkeypatch)


def scene(tmp_path, *, policy=role_share.DEFAULT, node=True, mode='soft', delivery=None, audit=None):
    public = tmp_path / 'public';public.mkdir(parents=True)
    objective = public / 'objective.md';objective.write_text('Spend the authorization mostly on experiments.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-call-a-model'], max_tokens=BIG, max_seconds=600, max_steps=12,
        model='no-model', reasoning_effort='high', search_policy=search.DEFAULT_POLICY, node_budget_mode=mode,
        node_budget_policy={role: dict(max_observed_tokens=500_000, max_seconds=60) for role in 'HWAE'} if node else None,
        delivery_policy=delivery, candidate_audit_policy=audit, role_budget_policy=policy)
    return root


def seal(root, step, role, parents, *, action='wait', tokens=10, plan=None, review=None, findings=None, recommendation=None, stage_intent='decision'):
    parents = list(parents)
    path = root / 'jobs' / f'{step:03d}.json'
    previous = c.read(path) if path.exists() else {}
    job = c.build_job(root, step, role, parents, previous.get('instruction', ''), previous.get('evidence_return'),
                      previous.get('correction_request'), previous.get('author_correction'))
    record = c.host_record(c.workspace(root, step))
    payload = dict(summary='A contribution', scientific_result='A bounded finding', limitations=['Fixture only'], implementation_selection=None)
    if role == 'H':payload.update(search_plan=plan)
    if role == 'A':payload.update(search_review=review, candidate_recommendation=recommendation, progress_assessment=None,
        decision_request=None, portfolio_review=dict(main_line=None,
            reason='Retained findings do not establish a preferred mechanism; preserve missing observations and defer further discriminating work.',
            discriminating_work=None))
    if role == 'A' and action == 'continue_w' and delivery_review.scope(job) == 'delivery_review':
        payload['decision_request'] = dict(decision='mechanism',
            question='Which missing observation changes this bounded finding?',
            change_criterion='A retained observation resolving the stated gap changes the assessment.')
    if role == 'W':
        nominations = (dict(representatives=[], extras=[], gaps=[dict(mechanism_ref=row['mechanism_ref'],
            reason='Fixture declares no scientific candidate suitability; the retained pool remains available to A.')
            for row in job['candidate_nomination_mechanisms'] if row['status'] != 'waived'])
            if job.get('candidate_nomination_mechanisms') else None)
        payload.update(stage_intent=stage_intent, mechanism_findings=findings, candidate_nominations=nominations,
            consultation_question='Which retained counterevidence changes the unfinished question?'
                if stage_intent in {'revise_h', 'review', 'extract'} else None)
    if role in {'H', 'A'}:payload.update(next_action=action, route_reason='Exact scientific question')
    intent = dict(task_ref=c.record_ref(path))
    c.write(record / 'native-intent.json', intent)
    accepted_budget.accept_native(root, step, record, intent)
    if isinstance(payload.get('candidate_recommendation'),str):payload['selection_explanation']=selection_explanation()
    fixture_final_coverage(root, job, payload)
    c.write(record / 'science-output.json', payload)
    c.write(record / 'usage.json', dict(input_tokens=tokens - 1, cached_input_tokens=0, output_tokens=1))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None, native_started=False, usage=native.usage(root),
                                                 raw_ref=c.file_ref(record / 'science-output.json')))
    c.seal_result(root, step)
    return job, payload


def parents(job):
    return [int(ref['path'].split('/')[2]) for ref in job['direct_refs']]


def drive(monkeypatch, script):
    """script[step] -> seal kwargs; any unscripted dispatch fails the test."""
    launched = []
    def execute(root, step):
        job = c.read(root / 'jobs' / f'{step:03d}.json');launched.append((step, job['role']))
        seal(root, step, job['role'], parents(job), **script[step])
    monkeypatch.setattr(native, 'execute_native', execute)
    return launched


def test_prepare_freezes_default_only_with_node_targets_and_validates(tmp_path):
    root = scene(tmp_path / 'a')
    assert c.settings(root)['role_budget_policy'] == role_share.DEFAULT
    assert 'role_budget_policy' not in c.settings(scene(tmp_path / 'b', node=False, policy=None))
    with pytest.raises(ValueError, match='node budgets'):
        scene(tmp_path / 'c', node=False, policy=dict(role_share.DEFAULT))
    for bad in (dict(role_share.DEFAULT, min_w_share=.7), dict(role_share.DEFAULT, max_thinking_share=0),
                dict(role_share.DEFAULT, thinking_node_target_tokens=0), dict(role_share.DEFAULT, version=2),
                dict(role_share.DEFAULT, extra=1)):
        with pytest.raises(ValueError):role_share.policy(bad)


def test_usage_counts_only_the_sealed_prefix_by_role(tmp_path):
    root = scene(tmp_path)
    seal(root, 1, 'W', [], tokens=900)
    seal(root, 2, 'H', [1], tokens=100)
    seal(root, 3, 'A', [1, 2], action='continue_w', tokens=40)
    assert role_share.usage(root, 2) == dict(H=100, W=900, A=0, E=0, closeout=0, thinking=100, unknown_steps=[], unknown_step_count=0)
    assert role_share.usage(root, 3)['thinking'] == 140


def test_share_and_repeat_reasons_spare_required_review(tmp_path, monkeypatch):
    root = scene(tmp_path)
    job, result = seal(root, 1, 'W', [], tokens=500_000)
    monkeypatch.setattr(role_share, 'usage', lambda *args: dict(thinking=3_500_000, E=0))
    assert role_share.reason(root, 1, job, result, 'A') is None
    assert role_share.reason(root, 1, job, dict(result, stage_intent='review'), 'A') == 'role_share_thinking_reached'
    assert role_share.reason(root, 1, job, result, 'H', correction_request=dict(trigger_id='x')) is None
    assert role_share.reason(root, 1, job, result, 'W') is None
    author = dict(job, role='A')
    assert role_share.reason(root, 1, author, {}, 'A') == 'role_repeat_without_evidence'


@pytest.mark.parametrize('thinking,extraction,expected', [
    (3_499_999, 0, None),
    (3_500_000, 0, 'role_share_thinking_reached'),
    (1_000_000, 2_500_000, 'role_share_w_floor_reached')])
def test_optional_share_counts_actual_usage_without_prepaying_unknown(tmp_path, monkeypatch, thinking, extraction, expected):
    root = scene(tmp_path)
    monkeypatch.setattr(role_share, 'usage', lambda *args: dict(thinking=thinking, E=extraction, unknown_steps=[1]))
    assert role_share.reason(root, 1, dict(role='W'), {}, 'H') == expected


def test_without_role_policy_preserves_optional_route(tmp_path):
    root = scene(tmp_path, policy=None)
    job, result = seal(root, 1, 'W', [], stage_intent='revise_h', tokens=9_000_000)
    route = controller._route(root, 1, 'W', result, job, None, None)
    assert 'role_budget_policy' not in c.settings(root)
    assert role_share.effective_route(root, 1, result, job, route) == (route, None)


@pytest.mark.parametrize('intent,next_role', [('revise_h', 'H'), ('review', 'A')])
def test_refused_worker_consultation_preserves_choice_without_dispatch(tmp_path, monkeypatch, intent, next_role):
    root = scene(tmp_path)
    launched = drive(monkeypatch, {1: dict(stage_intent='revise_h'),
        2: dict(tokens=3_500_000), 3: dict(stage_intent=intent, tokens=500_000)})
    result = controller.run(root)
    assert result['status'] == 'role_admission_refused' and result['next_role'] == next_role, result
    assert result['reason'] == 'role_share_thinking_reached'
    assert launched == [(1, 'W'), (2, 'H'), (3, 'W')] and not (root / 'jobs/004.json').exists()
    assert budget.closeout_state(root)['begin'] is None and budget.ceilings(root)['max_tokens'] == BIG
    assert controller.run(root)['status'] == 'role_admission_refused' and len(launched) == 3


def test_final_review_and_one_bounded_correction_ignore_optional_share(tmp_path, monkeypatch):
    root = scene(tmp_path)
    launched = drive(monkeypatch, {1: dict(tokens=500_000),
        2: dict(action='continue_w', tokens=3_500_000),
        3: dict(tokens=600_000), 4: dict(action='finish', tokens=10)})
    result = controller.run(root)
    assert result['status'] == 'finish', result
    assert launched == [(1, 'W'), (2, 'A'), (3, 'W'), (4, 'A')]
    for step, kind, used in ((2, 'delivery_review', 0), (3, 'delivery_correction', 1), (4, 'delivery_review', 1)):
        job = c.read(root / 'jobs' / f'{step:03d}.json')
        assert job['evidence_return'] == delivery_review.make(kind, step - 1, used)
        assert job.get('author_correction') is None
    final = c.read(root / 'jobs/004.json')
    assert set(c.result_schema('A', **c.response_options(final))['properties']['next_action']['enum']) == {'finish', 'deliver'}
    assert role_share.usage(root, 4)['thinking'] == 3_500_010
    assert controller.run(root)['status'] == 'finish' and len(launched) == 4


@pytest.mark.parametrize('blocked', ['unknown', 'tokens'])
def test_optional_consultation_cannot_bypass_unknown_or_shared_budget(tmp_path, monkeypatch, blocked):
    root = scene(tmp_path)
    launched = drive(monkeypatch, {1: dict(stage_intent='revise_h', tokens=100_000)})
    if blocked == 'tokens':
        budget.amend(root, max_tokens=100_000, authorization_note='Authorize exactly the first W observation')
    execute = native.execute_native
    def block_after_worker(root, step):
        assert step == 1, 'Blocked consultation launched native work'
        execute(root, step)
        if blocked == 'unknown':
            monkeypatch.setattr(budget, 'execution_blocked', lambda root, **kwargs: True)
            monkeypatch.setattr(role_share, 'usage', lambda *args: dict(thinking=3_500_000, E=0))
    monkeypatch.setattr(native, 'execute_native', block_after_worker)
    monkeypatch.setattr(controller, '_run_final', lambda root, begin, **kwargs: dict(status='final_boundary'))
    result = controller.run(root)
    assert launched == [(1, 'W')] and not (root / 'jobs/002.json').exists()
    assert native.usage(root)['observed_tokens'] == 100_000
    if blocked == 'unknown':
        assert result['status'] == 'waiting_execution' and budget.closeout_state(root)['begin'] is None, result
    else:
        assert result['status'] == 'final_boundary' and budget.closeout_state(root)['begin']['mode'] == 'final', result


def test_admission_correction_cannot_relabel_required_worker_review(tmp_path):
    from scidiscovery.research_loop import semantic
    root = scene(tmp_path)
    job, result = seal(root, 1, 'W', [])
    route = controller._route(root, 1, 'W', result, job, None, None)
    corrected = role_share.reconsider(root, 1, job, route, 'role_share_thinking_reached')
    with pytest.raises(ValueError, match='exact refused optional decision'):
        candidate = c.build_job(root, 2, 'H', list(corrected.parents), instruction=corrected.instruction,
            author_correction=corrected.author_correction)
        semantic.validate_job(root, candidate)


def test_role_share_uses_amended_total_and_preserves_route(tmp_path, monkeypatch):
    root = scene(tmp_path)
    job, result = seal(root, 1, 'W', [], stage_intent='revise_h')
    route = controller._route(root, 1, 'W', result, job, None, None)
    monkeypatch.setattr(role_share, 'usage', lambda *args: dict(thinking=3_500_000, E=0))
    assert role_share.effective_route(root, 1, result, job, route) == (route, 'role_share_thinking_reached')
    budget.amend(root, max_tokens=20_000_000, authorization_note='Increase future admission')
    assert role_share.effective_route(root, 1, result, job, route) == (route, None)
    budget.amend(root, max_tokens=10_000_000, authorization_note='Decrease future admission')
    assert role_share.reason(root, 1, job, result, 'H') == 'role_share_thinking_reached'


@pytest.mark.parametrize('accepted', [False, True])
def test_current_share_gates_prepared_but_never_replays_accepted_successor(tmp_path, monkeypatch, accepted):
    root = scene(tmp_path)
    job, result = seal(root, 1, 'W', [], stage_intent='review', tokens=100_000)
    route = controller._route(root, 1, 'W', result, job, None, None)
    c.build_job(root, 2, route.role, route.parents, instruction=route.instruction, evidence_return=route.evidence_return)
    if accepted:
        seal(root, 2, 'A', [1], action='continue_w', tokens=100_000)
    paths = [root / 'jobs/002.json']
    if accepted:
        paths += [c.host_record(c.workspace(root, 2)) / name for name in ('result-identity.json', 'usage.json', 'native-intent.json')]
    saved = {path: path.read_bytes() for path in paths}
    usage = native.usage(root)
    budget.amend(root, max_tokens=200_000, authorization_note='Reduce only future admission')
    monkeypatch.setattr(role_share, 'usage', lambda *args: dict(thinking=100_000, E=0))
    assert role_share.reason(root, 1, job, result, 'A') == 'role_share_thinking_reached'
    expected = None if accepted else 'role_share_thinking_reached'
    assert role_share.effective_route(root, 1, result, job, route) == (route, expected)
    assert native.usage(root) == usage and all(path.read_bytes() == raw for path, raw in saved.items())


def test_completed_current_route_keeps_accepted_originals_after_lower_total(tmp_path, monkeypatch):
    root = scene(tmp_path)
    launched = drive(monkeypatch, {1: dict(stage_intent='review', tokens=100_000),
        2: dict(action='continue_w', tokens=100_000), 3: dict(), 4: dict(action='finish')})
    assert controller.run(root)['status'] == 'finish'
    paths = [root / 'jobs' / f'{step:03d}.json' for step in range(1, 5)]
    paths += [c.host_record(c.workspace(root, step)) / name for step in range(1, 5)
              for name in ('result-identity.json', 'native-intent.json', 'usage.json')]
    originals = {path: path.read_bytes() for path in paths}
    usage = native.usage(root)
    budget.amend(root, max_tokens=200_000, authorization_note='Reduce future admission only')
    monkeypatch.setattr(native, 'execute_native', lambda *args: pytest.fail('Accepted work was replayed'))
    assert controller.run(root)['status'] == 'finish'
    assert launched == [(1, 'W'), (2, 'A'), (3, 'W'), (4, 'A')] and not (root / 'jobs/005.json').exists()
    assert native.usage(root) == usage and all(path.read_bytes() == raw for path, raw in originals.items())


def test_required_author_correction_route_bypasses_optional_share(tmp_path, monkeypatch):
    route = Route(role='A', parents=[1], author_correction=dict(reason='scientific_field_unresolved'))
    monkeypatch.setattr(role_share, 'reason', lambda *args, **kwargs: pytest.fail('Mandatory correction faced optional share admission'))
    assert role_share.effective_route(tmp_path, 1, {}, {}, route) == (route, None)


def test_retained_control_correction_unit_boundary_preserves_identity_after_amendment(tmp_path):
    from scidiscovery.research_loop import semantic
    root = scene(tmp_path)
    job, result = seal(root, 1, 'H', [], action='analyze', tokens=3_500_000)
    route = Route(role='A', parents=[1], instruction='Assess the exact retained question', current_h=1)
    code = role_share.reason(root, 1, job, result, route.role)
    assert code == 'role_share_thinking_reached'
    correction = role_share.reconsider(root, 1, job, route, code)
    retained = c.build_job(root, 2, correction.role, list(correction.parents),
        instruction=correction.instruction, author_correction=correction.author_correction)
    semantic.validate_job(root, retained)
    original = (root / 'jobs/002.json').read_bytes()
    assert not (c.host_record(c.workspace(root, 2)) / 'native-intent.json').exists()
    budget.amend(root, max_tokens=20_000_000, authorization_note='Change new admission, preserve the retained control correction')
    assert role_share.reason(root, 1, job, result, route.role) is None
    projected, refusal = role_share.effective_route(root, 1, result, job, route)
    assert refusal is None and projected == correction
    assert projected.author_correction['root'] == projected.author_correction['source'] == semantic._source(root, 1)
    assert (root / 'jobs/002.json').read_bytes() == original


def test_audit_receipt_review_remains_legal(tmp_path, monkeypatch):
    from scidiscovery.research_loop import candidate_audit
    root = scene(tmp_path)
    settings = dict(c.settings(root), candidate_audit_policy={'version': 1})
    monkeypatch.setattr(role_share.c, 'settings', lambda root, job=None: settings)
    monkeypatch.setattr(candidate_audit, 'receipt_ref', lambda root, step: None)
    job, result = dict(role='A'), dict(next_action='audit')
    assert role_share.reason(root, 1, job, result, 'A') == 'role_repeat_without_evidence'
    monkeypatch.setattr(candidate_audit, 'receipt_ref', lambda root, step: dict(step=step))
    assert role_share.reason(root, 1, job, result, 'A') is None


@pytest.fixture
def behavior_card():
    path = Path(__file__).resolve().parents[2] / 'scripts/acceptance/behavior_card.py'
    spec = importlib.util.spec_from_file_location('behavior_card', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_behavior_card_is_public_read_only_and_does_not_prepay_targets(tmp_path, behavior_card):
    root = scene(tmp_path, policy=None)
    seal(root, 1, 'W', [], tokens=500_000)
    seal(root, 2, 'A', [1], action='continue_w', tokens=3_300_000)
    runs = [dict(id=f'run-{n}', metric=m, direction='max', status='ok', evaluation_key='public-val')
            for n, m in ((1, .5), (2, .7), (3, .9))]
    observation = root / 'host-history/observations/001.json'
    c.write(observation, dict(candidates=runs, managed_calls=[{}, {}, {}]))
    paths = [root / 'settings.json', root / 'settings-identity.json', observation]
    for step in (1, 2):
        paths += [root / 'jobs' / f'{step:03d}.json', c.workspace(root, step) / 'science-task.json']
        paths += [c.host_record(c.workspace(root, step)) / name for name in
                  ('native-intent.json', 'native-outcome.json', 'science-output.json', 'result-identity.json', 'usage.json')]
    before = {path: path.read_bytes() for path in paths}
    value = behavior_card.card(root)
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert value['roles']['A']['tokens'] == 3_300_000 and value['sequence'] == 'W001 A002'
    assert value['registered_experiments'] == value['completed_public_runs'] == 3 and value['w_stages'] == 1
    pool = behavior_card.candidates(root)
    assert behavior_card.candidate_fact(pool, 'run-2', before=1) == dict(id='run-2', metric=.7, direction='max', key='public-val', step=1)
    assert behavior_card.candidate_fact(pool, 'run-2', before=0) is None
    assert 'best_public' not in value and 'rank' not in value['delivered'] and 'rank' not in behavior_card.text(value)
    assert value['default_policy_replay']['first_refusal'] is None
    c.write(observation, dict(candidates=[dict(runs[0], official_reward=1.)]))
    with pytest.raises(ValueError, match='Non-public'):
        behavior_card.card(root)


@pytest.mark.parametrize('dimension,at_limit', [('thinking', False), ('thinking', True), ('w_floor', False), ('w_floor', True)])
def test_behavior_card_replay_matches_actual_usage_at_share_boundary(tmp_path, behavior_card, dimension, at_limit):
    limits = dict(role_share.DEFAULT, min_w_share=.5, max_thinking_share=.25) if dimension == 'thinking' else dict(
        role_share.DEFAULT, min_w_share=.75, max_thinking_share=.2)
    root = scene(tmp_path, policy=limits)
    thinking = 2_500_000 - (not at_limit) if dimension == 'thinking' else 500_000
    extraction = 10 if dimension == 'thinking' else 2_000_000 - (not at_limit)
    jobs, nodes = {}, []
    script = [('W', 'revise_h', 10), ('H', None, thinking), ('W', 'extract', 10),
              ('E', None, extraction), ('W', 'revise_h', 10)]
    current_h = current_w = None
    for step, (role, intent, tokens) in enumerate(script, 1):
        if step == 1:
            parents_ = []
        else:
            c.build_job(root, step, role, route.parents, instruction=route.instruction, evidence_return=route.evidence_return)
            parents_ = route.parents
        job, result = seal(root, step, role, parents_, stage_intent=intent or 'decision', tokens=tokens)
        jobs[step] = job
        nodes.append(dict(step=step, role=role, tokens=tokens, next_action=result.get('next_action')))
        route = controller._route(root, step, role, result, job, current_h, current_w)
        current_h, current_w = route.current_h, route.current_w
    jobs[6] = c.build_job(root, 6, 'H', route.parents, instruction=route.instruction, evidence_return=route.evidence_return)
    nodes.append(dict(step=6, role='H', tokens=1, next_action='wait'))
    replay = behavior_card.replay(nodes, jobs, c.settings(root), limits)
    actual = role_share.reason(root, 5, job, result, 'H')
    assert actual == (f'role_share_{dimension}_reached' if at_limit else None)
    assert (replay['first_refusal'] or {}).get('refused') == actual
    assert replay['dispatches'][-1]['thinking_before'] == thinking


def test_cli_prepare_freezes_explicit_or_default_role_shares(tmp_path, capsys):
    from scidiscovery.research_loop import cli
    public = tmp_path / 'public';public.mkdir();objective = public / 'objective.md';objective.write_text('Bounded objective')
    node = tmp_path / 'node.json';c.write(node, {role: dict(max_observed_tokens=500_000, max_seconds=60) for role in 'HWAE'})
    custom = tmp_path / 'share.json';c.write(custom, dict(role_share.DEFAULT, max_thinking_share=.3))
    off = tmp_path / 'off.json';off.write_text('null\n')
    for name, extra, expected in (('d', [], None), ('c', ['--role-budget-policy', str(custom)], dict(role_share.DEFAULT, max_thinking_share=.3)),
                                  ('o', ['--role-budget-policy', str(off)], None)):
        assert cli.main(['prepare', '--root', str(tmp_path / name), '--objective', str(objective), '--public-root', str(public),
            '--native-launcher-json', '["never"]', '--node-budget-policy', str(node), '--max-tokens', '1000000',
            '--max-seconds', '60', '--max-steps', '4', *extra]) == 0
        assert c.settings(tmp_path / name).get('role_budget_policy') == expected
