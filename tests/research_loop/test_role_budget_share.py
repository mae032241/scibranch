"""Frozen role token shares route optional thinking by control; no model calls."""
from test_history_handoff import fixture_final_coverage, selection_explanation
import sys

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, native, role_share, search

BIG = 10_000_000
PLAN = dict(scope='Two mechanisms under one objective', commitments=[
    dict(label='P', minimal_test='Does P discriminate the observed shift?', existing_ref=None),
    dict(label='Q', minimal_test='Does Q discriminate the observed shift?', existing_ref=None)],
    focus=dict(label=None, action='screen'))
FINDINGS = [dict(mechanism=label, basis='blocked', finding='Fixture keeps a concrete missing observation for ' + label + '.')
            for label in ('P', 'Q')]


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


def seal(root, step, role, parents, *, action='wait', tokens=10, plan=None, review=None, findings=None, recommendation=None):
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
    if role == 'W':
        nominations = (dict(representatives=[], extras=[], gaps=[dict(mechanism_ref=row['mechanism_ref'],
            reason='Fixture declares no scientific candidate suitability; the retained pool remains available to A.')
            for row in job['candidate_nomination_mechanisms'] if row['status'] != 'waived'])
            if job.get('candidate_nomination_mechanisms') else None)
        payload.update(stage_intent='review', mechanism_findings=findings, candidate_nominations=nominations)
    if role in {'H', 'A'}:payload.update(next_action=action, route_reason='Exact scientific question')
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(path)))
    if isinstance(payload.get('candidate_recommendation'),str):payload['selection_explanation']=selection_explanation()
    fixture_final_coverage(root, job, payload)
    c.write(record / 'science-output.json', payload)
    c.write(record / 'usage.json', dict(input_tokens=tokens - 1, cached_input_tokens=0, output_tokens=1))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None, usage=native.usage(root),
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


def test_soft_thinking_node_target_is_a_hint_target_not_a_new_ceiling(tmp_path):
    soft = scene(tmp_path / 's')
    seal(soft, 1, 'H', [], action='experiment', plan=PLAN)
    h = dict(step=2, role='H');a = dict(step=2, role='A');w = dict(step=2, role='W')
    assert budget.node_limits(soft, h)['max_observed_tokens'] == budget.node_limits(soft, a)['max_observed_tokens'] == 250_000
    assert budget.node_limits(soft, w)['max_observed_tokens'] == 500_000


def test_usage_counts_only_the_sealed_prefix_by_role(tmp_path):
    root = scene(tmp_path)
    seal(root, 1, 'H', [], action='experiment', plan=PLAN, tokens=100)
    seal(root, 2, 'W', [1], findings=FINDINGS, tokens=900)
    seal(root, 3, 'A', [1, 2], action='revise_h', tokens=40)
    assert role_share.usage(root, 2) == dict(H=100, W=900, A=0, E=0, closeout=0, thinking=100, unknown_steps=[])
    assert role_share.usage(root, 3)['thinking'] == 140


def test_share_and_repeat_reasons_spare_required_review(tmp_path):
    root = scene(tmp_path)
    h, hp = seal(root, 1, 'H', [], action='experiment', plan=PLAN, tokens=100_000)
    w, wp = seal(root, 2, 'W', [1], findings=FINDINGS, tokens=500_000)
    a, ap = seal(root, 3, 'A', [1, 2], action='revise_h', tokens=3_100_000)
    assert role_share.reason(root, 3, a, ap, 'H') is None  # Actual thinking usage is 3.2M, below 3.5M.
    assert role_share.reason(root, 3, a, ap, 'A') == 'role_repeat_without_evidence'
    assert role_share.reason(root, 3, a, ap, 'H', correction_request=dict(trigger_id='x')) is None
    assert role_share.reason(root, 3, a, ap, 'W') is None
    a4, ap4 = seal(root, 4, 'H', [1, 2, 3], action='analyze', tokens=300_000)
    assert role_share.reason(root, 4, a4, ap4, 'A') == 'role_share_thinking_reached'
    assert role_share.reason(root, 2, w, wp, 'A') is None  # A after new W evidence is required review.


def test_w_floor_counts_extraction_against_non_w_usage(tmp_path):
    root = scene(tmp_path, policy=dict(role_share.DEFAULT, min_w_share=.6, max_thinking_share=.4))
    seal(root, 1, 'H', [], action='extract', plan=PLAN, tokens=1_000_000)
    e, ep = seal(root, 2, 'E', [1], tokens=3_000_000)
    assert role_share.reason(root, 2, e, ep, 'H') == 'role_share_w_floor_reached'


def test_optional_role_share_uses_actual_usage_without_prepaying_target_or_unknown(tmp_path,monkeypatch):
    root=scene(tmp_path)
    monkeypatch.setattr(role_share,'usage',lambda *args:dict(thinking=3_400_000,E=0,unknown_steps=[1]))
    assert role_share.reason(root,1,dict(role='A'),{},'H') is None
    monkeypatch.setattr(role_share,'usage',lambda *args:dict(thinking=3_500_000,E=0,unknown_steps=[1]))
    assert role_share.reason(root,1,dict(role='A'),{},'H')=='role_share_thinking_reached'


def test_without_role_policy_keeps_original_routes(tmp_path):
    root = scene(tmp_path, policy=None)
    assert 'role_budget_policy' not in c.settings(root)
    seal(root, 1, 'H', [], action='experiment', plan=PLAN, tokens=100_000)
    seal(root, 2, 'W', [1], findings=FINDINGS, tokens=100_000)
    a, ap = seal(root, 3, 'A', [1, 2], action='revise_h', tokens=9_000_000)
    assert role_share.reason(root, 3, a, ap, 'A') is None
    route = ('H', [1, 2, 3], 'x', None, 1, 2, None, None)
    assert role_share.effective_route(root, 3, ap, a, route) == (*route, None)


def test_refused_optional_node_returns_h_and_worker_choice_rejoins_normal_route(tmp_path, monkeypatch):
    root = scene(tmp_path)
    revised = dict(PLAN, commitments=[dict(item, existing_ref=f'h001.m{n}') for n, item in enumerate(PLAN['commitments'], 1)])
    launched = drive(monkeypatch, {
        1: dict(action='experiment', plan=PLAN, tokens=100_000),
        2: dict(findings=FINDINGS, tokens=500_000),
        3: dict(action='revise_h', tokens=3_400_000),
        4: dict(action='experiment', plan=revised, tokens=10),
        5: dict(findings=FINDINGS, tokens=10),
        6: dict(action='finish', tokens=10)})
    result = controller.run(root)
    assert result['status'] == 'finish', result
    assert launched == [(1, 'H'), (2, 'W'), (3, 'A'), (4, 'H'), (5, 'W'), (6, 'A')]
    request = c.read(root / 'jobs/004.json')['author_correction']
    assert request['root']['step'] == request['source']['step'] == 3
    assert request['reason'] == 'role_share_thinking_reached'
    task = c.read(c.workspace(root, 4) / 'science-task.json')
    assert task['control_request']['unresolved'][0]['code'] == request['reason'], task
    assert c.read(root / 'jobs/005.json').get('author_correction') is None
    assert budget.closeout_state(root)['begin'] is None and budget.ceilings(root)['max_tokens'] == BIG
    assert role_share.usage(root, 6)['thinking'] == 3_500_020
    before = list(launched)
    assert controller.run(root)['status'] == 'finish' and launched == before


def test_repeated_optional_refusal_keeps_first_root_and_finite_allowance(tmp_path, monkeypatch):
    root = scene(tmp_path)
    launched = drive(monkeypatch, {1: dict(action='analyze', tokens=3_500_000),
        2: dict(action='analyze', tokens=10), 3: dict(action='analyze', tokens=10)})
    result = controller.run(root)
    assert result['status'] == 'semantic_correction_required', result
    assert launched == [(1, 'H'), (2, 'H'), (3, 'H')]
    first = c.read(root / 'jobs/002.json')['author_correction']
    second = c.read(root / 'jobs/003.json')['author_correction']
    assert second['root'] == first['root'] and second['reason'] == first['reason'] == 'role_share_thinking_reached'
    assert second['source']['step'] == 2 and not (root / 'jobs/004.json').exists()
    assert role_share.usage(root, 3)['thinking'] == 3_500_020
    assert budget.ceilings(root)['max_tokens'] == BIG and budget.closeout_state(root)['begin'] is None
    assert controller.run(root)['status'] == 'semantic_correction_required'
    assert launched == [(1, 'H'), (2, 'H'), (3, 'H')]


@pytest.mark.parametrize('blocked', ['unknown', 'tokens'])
def test_optional_replanning_cannot_bypass_unknown_or_shared_budget(tmp_path, monkeypatch, blocked):
    root = scene(tmp_path)
    launched = drive(monkeypatch, {1: dict(action='experiment', plan=PLAN, tokens=100_000),
        2: dict(findings=FINDINGS, tokens=500_000), 3: dict(action='revise_h', tokens=3_400_000)})
    original_execute = native.execute_native
    def pause_after_author(root, step):
        original_execute(root, step)
        if step == 3:
            (root / 'pause.request').touch()
    monkeypatch.setattr(native, 'execute_native', pause_after_author)
    initial = controller.run(root)
    assert initial['status'] == 'paused' and launched == [(1, 'H'), (2, 'W'), (3, 'A')], initial
    (root / 'pause.request').unlink()
    if blocked == 'unknown':
        monkeypatch.setattr(budget, 'execution_blocked', lambda root, **kwargs: True)
    else:
        budget.amend(root, max_tokens=4_000_000, authorization_note='Retain exactly the consumed shared allowance')
    monkeypatch.setattr(native, 'execute_native', lambda *args: pytest.fail('Refused replan cannot launch native work'))
    monkeypatch.setattr(controller, '_run_final', lambda root, begin: dict(status='final_boundary'))
    result = controller.run(root)
    assert not (root / 'jobs/004.json').exists() and native.usage(root)['observed_tokens'] == 4_000_000
    if blocked == 'unknown':
        assert result['status'] == 'waiting_execution' and budget.closeout_state(root)['begin'] is None, result
    else:
        assert result['status'] == 'final_boundary' and budget.closeout_state(root)['begin']['mode'] == 'final', result


def test_admission_correction_cannot_relabel_required_worker_review(tmp_path):
    from scidiscovery.research_loop import semantic
    root = scene(tmp_path)
    seal(root, 1, 'H', [], action='experiment', plan=PLAN)
    job, _ = seal(root, 2, 'W', [1], findings=FINDINGS)
    route = ('A', [1, 2], 'Required worker review', None, 1, 2, None, None)
    corrected = role_share.reconsider(root, 2, job, route, 'role_share_thinking_reached')
    candidate = c.build_job(root, 3, 'H', corrected[1], instruction=corrected[2], author_correction=corrected[7])
    with pytest.raises(ValueError, match='exact refused optional decision'):
        semantic.validate_job(root, candidate)


def test_role_share_uses_effective_amended_total_and_preserves_route(tmp_path):
    root=scene(tmp_path)
    seal(root,1,'H',[],action='experiment',plan=PLAN,tokens=100_000)
    seal(root,2,'W',[1],findings=FINDINGS,tokens=500_000)
    job,result=seal(root,3,'A',[1,2],action='revise_h',tokens=3_400_000)
    route=('H',[1,2,3],'Retain the exact revision question',None,1,2,None,None)
    assert role_share.effective_route(root,3,result,job,route)==(*route,'role_share_thinking_reached')
    budget.amend(root,max_tokens=20_000_000,authorization_note='Explicit increase for the original route')
    assert role_share.effective_route(root,3,result,job,route)==(*route,None)
    budget.amend(root,max_tokens=10_000_000,authorization_note='Explicit decrease')
    assert role_share.reason(root,3,job,result,'H')=='role_share_thinking_reached'


def test_lower_total_preserves_already_accepted_ordinary_successor(tmp_path, monkeypatch):
    root = scene(tmp_path)
    launched = drive(monkeypatch, {1: dict(action='analyze', tokens=100_000),
        2: dict(action='finish', tokens=10)})
    assert controller.run(root)['status'] == 'finish'
    saved = {path: path.read_bytes() for step in (1, 2) for path in (
        root / 'jobs' / f'{step:03d}.json',
        c.host_record(c.workspace(root, step)) / 'result-identity.json',
        c.host_record(c.workspace(root, step)) / 'usage.json')}
    usage = native.usage(root)
    budget.amend(root, max_tokens=200_000, authorization_note='Reduce only future admission')
    job = c.read(root / 'jobs/001.json')
    result = c.validate(c.workspace(root, 1))
    assert role_share.reason(root, 1, job, result, 'A') == 'role_share_thinking_reached'
    monkeypatch.setattr(native, 'execute_native', lambda *args: pytest.fail('An accepted successor was replayed'))
    assert controller.run(root)['status'] == 'finish'
    assert launched == [(1, 'H'), (2, 'A')] and not (root / 'jobs/003.json').exists()
    assert native.usage(root) == usage and all(path.read_bytes() == raw for path, raw in saved.items())


def test_prepared_successor_without_acceptance_still_faces_current_shares(tmp_path):
    root = scene(tmp_path)
    job, result = seal(root, 1, 'H', [], action='analyze', tokens=100_000)
    route = ('A', [1], result['route_reason'], None, 1, None, None, None)
    c.build_job(root, 2, 'A', [1], instruction=route[2])
    budget.amend(root, max_tokens=200_000, authorization_note='Prepared work has no native acceptance')
    assert not (c.host_record(c.workspace(root, 2)) / 'native-intent.json').exists()
    assert role_share.effective_route(root, 1, result, job, route) == (*route, 'role_share_thinking_reached')


def test_budget_amendment_preserves_retained_admission_correction(tmp_path,monkeypatch):
    root = scene(tmp_path)
    initial_calls = drive(monkeypatch, {1: dict(action='experiment', plan=PLAN, tokens=100_000),
        2: dict(findings=FINDINGS, tokens=500_000), 3: dict(action='revise_h', tokens=3_400_000)})
    build_job = c.build_job
    def pause_prepared_correction(root, step, *args, **kwargs):
        job = build_job(root, step, *args, **kwargs)
        if step == 4:
            (root / 'pause.request').touch()
        return job
    monkeypatch.setattr(c, 'build_job', pause_prepared_correction)
    initial = controller.run(root)
    assert initial['status'] == 'paused' and initial_calls == [(1, 'H'), (2, 'W'), (3, 'A')], initial
    monkeypatch.setattr(c, 'build_job', build_job)
    original = (root / 'jobs/004.json').read_bytes()
    budget.amend(root, max_tokens=20_000_000, authorization_note='Raise the total without rewriting the accepted host refusal')
    job = c.read(root / 'jobs/003.json')
    result = c.validate(c.workspace(root, 3))
    assert role_share.reason(root, 3, job, result, 'H') is None
    revised = dict(PLAN, commitments=[dict(item, existing_ref=f'h001.m{n}') for n, item in enumerate(PLAN['commitments'], 1)])
    launched = drive(monkeypatch, {4: dict(action='experiment', plan=revised, tokens=10),
        5: dict(findings=FINDINGS, tokens=10), 6: dict(action='finish', tokens=10)})
    (root / 'pause.request').unlink()
    resumed = controller.run(root)
    assert resumed['status'] == 'finish', resumed
    assert launched == [(4, 'H'), (5, 'W'), (6, 'A')]
    assert (root / 'jobs/004.json').read_bytes() == original
    assert budget.closeout_state(root)['begin'] is None and budget.ceilings(root)['max_tokens'] == 20_000_000


def test_controller_preserves_required_author_correction(tmp_path, monkeypatch):
    root = scene(tmp_path)
    repair = dict(PLAN, focus=dict(label='Q', action='repair'))
    # The unresolved first repair commits no mechanisms. Correct the request
    # with fresh declarations rather than references to an uncommitted H.
    corrected = PLAN
    launched = drive(monkeypatch, {
        1: dict(action='experiment', plan=repair, tokens=3_400_000),
        2: dict(action='experiment', plan=corrected, tokens=100_000),
        3: dict(findings=FINDINGS, tokens=600_000),
        4: dict(action='finish', tokens=100_000)})
    result = controller.run(root)
    assert result['status'] == 'finish', result
    assert launched == [(1, 'H'), (2, 'H'), (3, 'W'), (4, 'A')]
    job = c.read(root / 'jobs/002.json')
    assert job['author_correction']['reason'] == 'search_repair_source_not_direct'
    assert 'role_budget' not in job
    assert {row['source_h'] for row in c.read(root/'jobs/003.json')['search']['breadth']['mechanisms']}=={2}


def test_required_corrections_keep_the_original_retry_cap(tmp_path, monkeypatch):
    root = scene(tmp_path)
    repair = dict(PLAN, focus=dict(label='Q', action='repair'))
    revised = dict(repair, commitments=[dict(item, existing_ref=f'h001.m{n}') for n, item in enumerate(PLAN['commitments'], 1)])
    launched = drive(monkeypatch, {
        1: dict(action='experiment', plan=repair, tokens=3_400_000),
        2: dict(action='experiment', plan=revised, tokens=100_000),
        3: dict(action='experiment', plan=revised, tokens=100_000)})
    result = controller.run(root)
    assert result['status'] == 'semantic_correction_required', result
    assert launched == [(1, 'H'), (2, 'H'), (3, 'H')]
    before = list(launched)
    assert controller.run(root)['status'] == 'semantic_correction_required'
    assert launched == before


def test_no_valid_w_stops_without_dispatching_thinking(tmp_path, monkeypatch):
    root = scene(tmp_path)
    waived = dict(dispositions=[dict(mechanism_ref=ref, status='waived', reason='Fixture closes this branch') for ref in ('h001.m1', 'h001.m2')],
                  next_focus=None)
    launched = drive(monkeypatch, {1: dict(action='experiment', plan=PLAN, tokens=100_000), 2: dict(findings=FINDINGS, tokens=500_000),
        3: dict(action='revise_h', review=waived, tokens=3_400_000),
        4: dict(action='wait', tokens=10)})
    final = []
    monkeypatch.setattr(controller, '_run_final',
        lambda root, begin: final.append(begin) or dict(status='final_report'))
    result = controller.run(root)
    assert result['status'] == 'final_report'
    begin = budget.closeout_state(root)['begin']
    assert final == [begin] and begin['mode'] == 'final'
    assert begin['stop_reason'] == 'scientific_wait' and begin['source_cutoff'] == 4
    assert launched == [(1, 'H'), (2, 'W'), (3, 'A'), (4, 'H')] and not (root / 'jobs/005.json').exists()


def test_no_valid_w_with_delivery_enters_durable_final(tmp_path, monkeypatch):
    delivery = dict(closeout_a=dict(max_observed_tokens=1000, max_seconds=30), inflight_tokens=100, cleanup_seconds=5,
                    submit_seconds=0, confirm_seconds=0, adapter=None)
    root = scene(tmp_path, delivery=delivery)
    waived = dict(dispositions=[dict(mechanism_ref=ref, status='waived', reason='Fixture closes this branch') for ref in ('h001.m1', 'h001.m2')],
                  next_focus=None)
    launched=drive(monkeypatch, {1: dict(action='experiment', plan=PLAN, tokens=100_000), 2: dict(findings=FINDINGS, tokens=500_000),
        3: dict(action='revise_h', review=waived, tokens=3_400_000),
        4: dict(action='wait', tokens=10)})
    calls = []
    monkeypatch.setattr(controller, '_run_final', lambda root, begin: calls.append(begin) or dict(status='final_report'))
    assert controller.run(root)['status'] == 'final_report'
    begin=budget.closeout_state(root)['begin']
    assert calls==[begin] and begin['mode']=='final' and begin['source_cutoff']==4 and begin['first_step']==5
    assert begin['stop_reason']=='scientific_wait'
    assert launched==[(1,'H'),(2,'W'),(3,'A'),(4,'H')] and not (root/'jobs/005.json').exists()


def test_audit_receipt_review_remains_legal(tmp_path, monkeypatch):
    root = scene(tmp_path)
    seal(root, 1, 'H', [], action='experiment', plan=PLAN, tokens=100_000)
    seal(root, 2, 'W', [1], findings=FINDINGS, tokens=500_000)
    a, ap = seal(root, 3, 'A', [1, 2], action='audit', tokens=3_300_000)
    assert role_share.reason(root, 3, a, ap, 'A') == 'role_repeat_without_evidence'  # No audit policy, no receipt.
    from scidiscovery.research_loop import candidate_audit
    settings = dict(c.settings(root), candidate_audit_policy={'version': 1})
    monkeypatch.setattr(role_share.c, 'settings', lambda root, job=None: settings)
    monkeypatch.setattr(candidate_audit, 'receipt_ref', lambda root, step: dict(step=step) if step == 3 else None)
    assert role_share.reason(root, 3, a, ap, 'A') is None


def test_behavior_card_is_read_only_public_and_replays_the_frozen_share(tmp_path):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('behavior_card', Path(__file__).resolve().parents[2] / 'scripts/acceptance/behavior_card.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    root = scene(tmp_path, policy=None)
    seal(root, 1, 'H', [], action='experiment', plan=PLAN, tokens=100_000)
    seal(root, 2, 'W', [1], findings=FINDINGS, tokens=500_000)
    seal(root, 3, 'A', [1, 2], action='revise_h', tokens=3_300_000)
    seal(root, 4, 'H', [1, 2, 3], action='wait', tokens=200_000)
    runs = [dict(id=f'run-{n}', metric=m, direction='max', status='ok', evaluation_key='public-val') for n, m in ((1, .5), (2, .7), (3, .9))]
    c.write(root / 'host-history/observations/002.json', dict(candidates=runs, managed_calls=[{}, {}, {}]))
    before = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    value = module.card(root)
    assert {path: path.read_bytes() for path in root.rglob('*') if path.is_file()} == before
    assert value['roles']['A']['tokens'] == 3_300_000 and value['sequence'] == 'H001 W002 A003 H004'
    assert value['registered_experiments'] == 3 and value['w_stages'] == 1 and value['completed_public_runs'] == 3
    pool = module.candidates(root)
    assert module.candidate_fact(pool,'run-2',before=2)==dict(id='run-2',metric=.7,direction='max',key='public-val',step=2)
    assert module.candidate_fact(pool,'run-2',before=1) is None
    assert 'best_public' not in value and 'rank' not in value['delivered']
    assert 'rank' not in module.text(value)
    # Replay follows actual dispatch usage; a future target is not prepaid.
    assert value['default_policy_replay']['first_refusal'] is None
    c.write(root / 'host-history/observations/003.json', dict(candidates=[dict(runs[0], official_reward=1.0)]))
    with pytest.raises(ValueError, match='Non-public'):module.card(root)


@pytest.mark.parametrize('dimension,at_limit', [('thinking', False), ('thinking', True), ('w_floor', False), ('w_floor', True)])
def test_behavior_card_replay_matches_actual_usage_at_exact_share_boundary(tmp_path, dimension, at_limit):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('behavior_card_boundary', Path(__file__).resolve().parents[2] / 'scripts/acceptance/behavior_card.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    limits = dict(role_share.DEFAULT, min_w_share=.5, max_thinking_share=.25) if dimension == 'thinking' else dict(
        role_share.DEFAULT, min_w_share=.75, max_thinking_share=.2)
    root = scene(tmp_path, policy=limits)
    h_tokens = 2_500_000 - (not at_limit) if dimension == 'thinking' else 500_000
    e_tokens = 10 if dimension == 'thinking' else 2_000_000 - (not at_limit)
    h, _ = seal(root, 1, 'H', [], action='extract', plan=PLAN, tokens=h_tokens)
    e, result = seal(root, 2, 'E', [1], tokens=e_tokens)
    next_job = c.build_job(root, 3, 'H', [1, 2])
    nodes = [dict(step=1, role='H', tokens=h_tokens, next_action='extract'),
        dict(step=2, role='E', tokens=e_tokens, next_action=None),
        dict(step=3, role='H', tokens=1, next_action='wait')]
    replay = module.replay(nodes, {1:h, 2:e, 3:next_job}, c.settings(root), limits)
    actual = role_share.reason(root, 2, e, result, 'H')
    assert actual == (f'role_share_{dimension}_reached' if at_limit else None)
    assert (replay['first_refusal'] or {}).get('refused') == actual
    assert replay['dispatches'][0]['thinking_before'] == h_tokens


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
