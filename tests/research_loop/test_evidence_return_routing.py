"""Extraction returns a new scientific decision through real sealed handoffs."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import accepted_budget, contracts, controller, native
from test_direct_input_bindings import stopped_seals


QUESTIONS = (
    'Choose extract: obtain the calibration source for the original temperature condition.',
    'Choose extract: obtain the independent sample identity for the revised mechanism.',
)
LIMITATION = 'Synthetic routing fixture; scientific sufficiency is not asserted.'


def _seal_output(root, step, role, action, reason, *, missing=False):
    job_path = root / 'jobs' / f'{step:03d}.json'
    job = contracts.read(job_path)
    assert job['role'] == role
    work = contracts.workspace(root, step)
    record = contracts.host_record(work)
    payload = dict(summary=f'Synthetic {role} contribution {step}',
        scientific_result='Requested source is unavailable; no values were inferred.'
            if missing else 'A synthetic bounded contribution.',
        limitations=[LIMITATION, 'Missing requested source'] if missing else [LIMITATION],
        implementation_selection=None)
    if role in {'H', 'A'}:
        payload.update(next_action=action, route_reason=reason)
        payload['search_plan' if role == 'H' else 'search_review'] = None
    if role == 'A':
        payload.update(candidate_recommendation=None, selection_explanation=None,
            progress_assessment=None, decision_request=(dict(decision='mechanism',question=reason,
                change_criterion='The exact requested source resolves or preserves this bounded evidence gap.')
                if action in {'extract','revise_h'} else None), portfolio_review=None,
            final_coverage=None)
    if role == 'W':
        # A continuing legacy W asks ordinary assessment, not W consultation or Final A.
        payload.update(stage_intent='continue', mechanism_findings=None, candidate_nominations=None)
    if role == 'E' and not missing:
        (work / 'artifacts' / f'source-{step}.txt').write_text('Synthetic requested source.')
    intent = dict(task_ref=contracts.record_ref(job_path))
    contracts.write(record / 'native-intent.json', intent)
    accepted_budget.accept_native(root, step, record, intent)
    contracts.write(record / 'science-output.json', payload)
    contracts.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    contracts.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None,
        native_started=False, usage=native.usage(root),
        raw_ref=contracts.file_ref(record / 'science-output.json')))
    contracts.seal_result(root, step)
    return (record / 'science-output.json').read_bytes()


@pytest.mark.parametrize('requester_role', ['H', 'A'])
@pytest.mark.parametrize('missing', [False, True], ids=['source-returned', 'source-missing'])
def test_evidence_return_reassesses_exact_request_and_allows_new_extraction(
        tmp_path, monkeypatch, requester_role, missing):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Resolve the original question within its temperature constraint.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fake'], max_tokens=10000,
        max_seconds=300, max_steps=12, model='fixture', reasoning_effort='high',
        search_policy=None, experiment_mode='external', research_strategy_version=2)
    assert contracts.settings(root)['research_strategy_version']==2
    assert 'advisory_scope_version' not in contracts.settings(root)
    stopped_seals(monkeypatch)
    if requester_role == 'H':
        plan = [('H', 'extract', QUESTIONS[0]), ('E', '', ''),
            ('H', 'extract', QUESTIONS[1]), ('E', '', ''),
            ('H', 'analyze', 'Assess the returned sources and original constraints.'),
            ('A', 'wait', 'The synthetic fixture leaves the scientific question open.')]
        requester_steps = (1, 3)
        expected_parents = ([1, 2], [3, 4])
    else:
        plan = [('H', 'experiment', 'Explore the original constrained question.'),
            ('W', '', ''), ('A', 'extract', QUESTIONS[0]), ('E', '', ''),
            ('A', 'extract', QUESTIONS[1]), ('E', '', ''),
            ('A', 'revise_h', 'Reassess the direction using the returned sources.'),
            ('H', 'analyze', 'Assess the revised scientific direction.'),
            ('A', 'wait', 'The synthetic fixture leaves the scientific question open.')]
        requester_steps = (3, 5)
        expected_parents = ([1, 2, 3, 4], [1, 2, 5, 6])
    calls = []
    originals = {}

    def execute(instance, step):
        calls.append(step)
        role, action, reason = plan[step - 1]
        originals[step] = _seal_output(instance, step, role, action, reason,
            missing=missing and step == requester_steps[0] + 1)

    monkeypatch.setattr(native, 'execute_native', execute)
    result = controller.run(root)
    assert result['status'] == 'wait'
    assert result['goal_completion'] == 'not_asserted'
    assert result['limitations'] == [LIMITATION]
    assert calls == list(range(1, len(plan) + 1))

    retained_jobs = {}
    for index, (requester_step, parents) in enumerate(zip(requester_steps, expected_parents)):
        extraction_step, return_step = requester_step + 1, requester_step + 2
        extraction = contracts.read(root / 'jobs' / f'{extraction_step:03d}.json')
        assert extraction['evidence_return'] == dict(role=requester_role, step=requester_step)
        assert extraction['instruction'] == QUESTIONS[index]
        job_path = root / 'jobs' / f'{return_step:03d}.json'
        returned = contracts.read(job_path)
        retained_jobs[job_path] = job_path.read_bytes()
        assert returned['role'] == requester_role
        assert returned['evidence_return'] is None
        assert [int(Path(row['path']).parts[2]) for row in returned['direct_refs']] == parents
        assert returned['instruction'] != QUESTIONS[index]
        assert 'Choose extract' not in returned['instruction']
        assert 'missing evidence and limitations' in returned['instruction']
        assert 'choose the next scientific route' in returned['instruction']
        requester_ref = f'inputs/parents/{requester_step:03d}/conclusion.json'
        extraction_ref = f'inputs/parents/{extraction_step:03d}/conclusion.json'
        assert requester_ref in returned['instruction']
        assert extraction_ref in returned['instruction']
        view = contracts.read(contracts.workspace(root, return_step) / 'science-task.json')
        assert view['question'] == returned['instruction']
        assert {requester_ref, extraction_ref} <= {row['conclusion'] for row in view['parents']}
        requester_record = contracts.host_record(contracts.workspace(root, requester_step))
        assert (requester_record / 'science-output.json').read_bytes() == originals[requester_step]
        assert contracts.read(requester_record / 'science-output.json')['route_reason'] == QUESTIONS[index]
        if index == 0 and missing:
            assert any('Missing requested source' in row['limitations']
                for row in view['parents'] if row['conclusion'] == extraction_ref)

    assert controller.run(root)['status'] == 'wait'
    assert calls == list(range(1, len(plan) + 1))
    assert all(path.read_bytes() == body for path, body in retained_jobs.items())
