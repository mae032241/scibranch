"""Scientific decision declarations, exact request admission and bounded clarification."""
import copy
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c, controller, correction, decision, native, protocol, semantic
from test_history_handoff import finish, fixture_portfolio_review, fixture_final_coverage

REQUEST = dict(decision='candidate', question='Should the candidate be retained under the declared target condition?',
               change_criterion='A controlled contradiction would change the candidate choice.')


def prepare(tmp_path, *, enabled=True, correction_enabled=False):
    from scidiscovery.research_loop import search
    public = tmp_path / 'public'; public.mkdir(parents=True)
    objective = public / 'objective.md'; objective.write_text('Compare mechanisms without abandoning required controls.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-launched'], max_tokens=100_000,
        max_seconds=3600, max_steps=16, model='fixture', reasoning_effort='high',
        decision_contract_version=1 if enabled else None,
        search_policy=search.DEFAULT_POLICY if correction_enabled else None,
        correction_policy=dict(review_after=1, pause_after=1) if correction_enabled else None)
    plan = dict(scope='Target discrimination', commitments=[dict(label='M',minimal_test='Required control',existing_ref=None)],
                focus=dict(label='M',action='screen')) if correction_enabled else None
    finish(root, 1, 'H', [], action='experiment', search_choice=plan)
    job = c.build_job(root, 2, 'W', [1], instruction='Exact scientific question')
    findings = [dict(mechanism=row['mechanism_ref'], basis='blocked',
                     finding='Fixture has no solver observations for this assigned mechanism; target uncertainty remains.')
                for row in (job.get('search') or {}).get('coverage_obligation', [])]
    text = ('No experiment observations were produced; target uncertainty remains.' if findings
            else 'Negative controlled result; target uncertainty remains.')
    finish(root, 2, 'W', [1], text=text,
           mechanism_findings=findings or None)
    return root


def author(root, step, parents, request, *, action='continue_w', review=None, omit_request=False):
    previous = c.read(root / 'jobs' / f'{step:03d}.json') if (root / 'jobs' / f'{step:03d}.json').exists() else {}
    job = c.build_job(root, step, 'A', parents, instruction=previous.get('instruction', ''),
                      author_correction=previous.get('author_correction'))
    record = c.host_record(c.workspace(root, step))
    payload = dict(summary='Global decision with limitations', scientific_result='Explain the scientific decision and remaining gap.',
                   limitations=['No target conclusion from the proxy alone'], implementation_selection=None,
                   search_review=review, progress_assessment=dict(verdict='no_progress'),
                   candidate_recommendation=None, next_action=action, route_reason='Exact scientific question',
                   decision_request=request, portfolio_review=None)
    if omit_request:
        payload.pop('decision_request')
    fixture_portfolio_review(root, job, payload)
    fixture_final_coverage(root, job, payload)
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs' / f'{step:03d}.json')))
    c.write(record / 'science-output.json', payload)
    c.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None, native_started=False, usage=native.usage(root),
                                             raw_ref=c.file_ref(record / 'science-output.json')))
    c.seal_result(root, step)
    return job, payload


def next_worker(root, *, text):
    c.build_job(root, 4, 'W', [1, 2, 3], instruction='Exact scientific question')
    finish(root, 4, 'W', [1, 2, 3], text=text)


@pytest.mark.parametrize('action', ['continue_w', 'revise_h', 'extract'])
def test_minimal_route_does_not_require_optional_decision_request(tmp_path, action):
    root = prepare(tmp_path)
    job, payload = author(root, 3, [1, 2], None, action=action, omit_request=True)
    validated = c.validate(c.workspace(root, 3))
    assert validated['next_action'] == action and 'decision_request' not in validated
    outcome = c.read(c.host_record(c.workspace(root, 3)) / 'result-identity.json')['control_outcome']
    assert outcome['decision_status'] == 'applied'
    assert decision.reason(root, job, payload) is None


def test_minimal_route_still_rejects_completed_repetition_without_new_evidence(tmp_path):
    root = prepare(tmp_path)
    author(root, 3, [1, 2], None, omit_request=True)
    next_worker(root, text='A retained response to the original scientific question.')
    # Reassessing the old exact W adds no evidence to that completed request.
    job, payload = author(root, 5, [1, 2, 3], None, omit_request=True)
    assert decision.reason(root, job, payload) == 'a_decision_request_repeated'
    changed = dict(payload, route_reason='A different scientific question for the same evidence.')
    assert decision.reason(root, job, changed) is None


@pytest.mark.parametrize('action', ['continue_w', 'revise_h', 'extract'])
def test_missing_scientific_declaration_preserves_raw_and_returns_to_a(tmp_path, action):
    root = prepare(tmp_path)
    job, payload = author(root, 3, [1, 2], None, action=action)
    record = c.host_record(c.workspace(root, 3))
    raw = (record / 'science-output.json').read_bytes()
    assert c.validate(c.workspace(root, 3))['next_action'] == action
    receipt = c.read(record / 'result-identity.json')['control_outcome']
    assert receipt['decision_status'] == 'unresolved'
    route = controller._route(root, 3, 'A', payload, job, 1, 2)
    route = semantic.effective_route(root, 3, payload, job, (*route, None))
    assert route[0] == 'A' and route[7]['reason'] == 'a_decision_request_missing'
    following = c.build_job(root, 4, 'A', route[1], instruction=route[2], author_correction=route[7])
    assert following['author_correction']['root']['step'] == 3
    assert (record / 'science-output.json').read_bytes() == raw
    assert not (c.host_record(c.workspace(root, 4)) / 'native-intent.json').exists()


def test_exact_completed_request_without_new_original_is_rejected_but_negative_new_result_is_allowed(tmp_path):
    root = prepare(tmp_path)
    original, original_payload = author(root, 3, [1, 2], REQUEST)
    prior_raw = c.read(c.host_record(c.workspace(root, 2)) / 'science-output.json')['scientific_result']
    next_worker(root, text=prior_raw)
    # Reassess the same original W, rather than treating equal bytes at a new
    # producer as proof that the scientific source is the same.
    job, payload = author(root, 5, [1, 2, 3], copy.deepcopy(REQUEST))
    assert c.read(c.host_record(c.workspace(root, 5)) / 'result-identity.json')['control_outcome']['decision_status'] == 'unresolved'
    assert decision.reason(root, job, payload) == 'a_decision_request_repeated'
    # A different negative finding is new evidence; no proxy/unknown-value filter is applied.
    root2 = prepare(tmp_path / 'other')
    author(root2, 3, [1, 2], REQUEST)
    next_worker(root2, text='New controlled counterexample contradicts retention; target performance is still unknown.')
    job2, payload2 = author(root2, 5, [1, 4], REQUEST)
    assert decision.reason(root2, job2, payload2) is None
    assert c.read(c.host_record(c.workspace(root2, 5)) / 'result-identity.json')['control_outcome']['decision_status'] == 'applied'
    focus = c.read(c.host_record(c.workspace(root, 3)) / 'result-identity.json')['control_outcome']['resolved_focus']
    assert focus is None  # This instance has no search policy or resolved mechanism.
    assert decision._key(original, original_payload, focus)[-2] is None


@pytest.mark.parametrize('action', ['audit', 'finish', 'wait'])
def test_nonresearch_choices_do_not_need_decision_request(tmp_path, action):
    root = prepare(tmp_path)
    job, payload = author(root, 3, [1, 2], None, action=action)
    assert decision.reason(root, job, payload) is None
    assert c.read(c.host_record(c.workspace(root, 3)) / 'result-identity.json')['control_outcome']['decision_status'] == 'applied'


def test_no_completed_dependency_does_not_consume_request_and_changed_prose_is_not_semantic_duplicate(tmp_path):
    root = prepare(tmp_path)
    author(root, 3, [1, 2], REQUEST)
    job, payload = author(root, 4, [1, 2, 3], REQUEST)
    assert decision.reason(root, job, payload) is None  # A itself is not a W response.
    assert decision._completed(c.read(root/'jobs/003.json'), c.read(c.host_record(c.workspace(root,3))/'science-output.json'),
                               {4: (job, payload, {}, '')}) is False
    changed = copy.deepcopy(payload)
    changed['decision_request']['question'] += ' Is there another explanation?'
    focus = c.read(c.host_record(c.workspace(root, 4)) / 'result-identity.json')['control_outcome']['resolved_focus']
    assert focus is None  # Both requests assess the same absence of search focus.
    assert decision._key(job, changed, focus) != decision._key(job, payload, focus)


def test_real_controller_clarification_is_bounded_and_not_counted_as_progress(tmp_path, monkeypatch):
    root = prepare(tmp_path, correction_enabled=True)
    record = c.host_record(c.workspace(root, 2))
    delivery = c.read(record / 'result-identity.json')['control_outcome']['worker_delivery']
    assert delivery['status'] == 'partial' and delivery['reason'] == 'scientific_blocked'
    assert c.read(record / 'work-evidence.json')['evidence'] == []
    review = dict(dispositions=[], next_focus=dict(mechanism_ref='h001.m1', action='screen'))
    # Partial W delivery supplies the exact question retained by the A job.
    route = controller._route(root, 2, 'W', c.validate(c.workspace(root, 2)),
                              c.read(root / 'jobs/002.json'), 1, None)
    assert route[0] == 'A' and route[1] == [1, 2]
    c.build_job(root, 3, 'A', route[1], instruction=route[2])
    author(root, 3, [1, 2], None, review=review)
    controller._check_job(root, c.settings(root), 3, 'A', route[1], route[2], route[3])
    assert correction.project(root)['eligible'] == []
    accepted = []
    def respond(root, step):
        job = c.read(root / 'jobs' / f'{step:03d}.json')
        assert job['role'] == 'A' and job['author_correction']['root']['step'] == 3
        accepted.append(step)
        parents = [int(Path(row['path']).parts[2]) for row in job['direct_refs']]
        author(root, step, parents, None, review=review)
    monkeypatch.setattr(native, 'execute_native', respond)
    result = controller.run(root)
    assert result['status'] == 'semantic_correction_required' and result['reason'] == 'semantic_correction_limit', result
    assert accepted == [4, 5] and result['completed_steps'] == 5
    assert correction.project(root)['eligible'] == []
    before = [(root / 'jobs' / f'{step:03d}.json').read_bytes() for step in (3, 4, 5)]
    assert controller.run(root)['status'] == 'semantic_correction_required'
    assert accepted == [4, 5]
    assert before == [(root / 'jobs' / f'{step:03d}.json').read_bytes() for step in (3, 4, 5)]


def test_minimal_schema_and_current_tasks_stay_frozen_without_decision_contract(tmp_path):
    schema = c.result_schema('A')
    assert schema['additionalProperties'] is True
    assert set(schema['required']) == {'summary', 'scientific_result', 'limitations', 'next_action', 'route_reason'}
    assert 'decision_request' not in schema['properties']
    root = prepare(tmp_path, enabled=False)
    job, _ = author(root, 3, [1, 2], None, action='wait')
    originals = {path:path.read_bytes() for path in (root/'settings.json', root/'jobs/003.json',
                 c.workspace(root,3)/'science-task.json', c.host_record(c.workspace(root,3))/'result-identity.json')}
    assert job['handoff_protocol'] == protocol.CURRENT and 'decision_contract_version' not in c.settings(root)
    assert c.build_job(root, 3, 'A', [1, 2]) == job
    assert all(path.read_bytes() == raw for path,raw in originals.items())
    assert c.build_job(root, 4, 'H', [3])['handoff_protocol'] == protocol.CURRENT
    assert all(path.read_bytes() == raw for path,raw in originals.items())


@pytest.mark.parametrize('retired', [8, 9, 10])
def test_retired_schemas_and_task_writes_reject_without_changing_originals(tmp_path, retired):
    with pytest.raises(protocol.UnsupportedProtocol, match='Scientific result schema'):
        c.result_schema('A', retired)
    # Preserved archive bytes are fixtures, not an invocation of a retired writer.
    root = tmp_path / 'archive'
    c.write(root / 'settings.json', dict(handoff_protocol=retired))
    c.write(root / 'settings-identity.json', dict(path='settings.json'))
    job = dict(step=3, role='A', handoff_protocol=retired)
    c.write(root / 'jobs/003.json', job)
    c.write(c.workspace(root, 3) / 'science-task.json', dict(preserved_task='Original scientific limitations'))
    c.write(c.host_record(c.workspace(root, 3)) / 'result-identity.json', dict(preserved_seal='Original identity'))
    paths = [root / 'settings.json', root / 'settings-identity.json', root / 'jobs/003.json',
             c.workspace(root, 3) / 'science-task.json', c.host_record(c.workspace(root, 3)) / 'result-identity.json']
    def snapshot():
        return {path.relative_to(root): (path.lstat().st_mode, path.read_bytes()) for path in paths}
    before = snapshot()
    for step, role, parents in [(3, 'A', [1, 2]), (4, 'H', [3])]:
        with pytest.raises(protocol.UnsupportedProtocol, match='Instance'):
            c.build_job(root, step, role, parents)
        assert snapshot() == before
