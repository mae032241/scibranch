"""Ordinary A returns advice without authoring W's next scientific decision."""

import pytest

from scidiscovery.research_loop import accepted_budget, contracts as c, controller, decision, native, protocol
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_history_handoff import finish, fixture_portfolio_review, fixture_final_coverage

REQUEST = dict(decision='candidate', question='Should the candidate be retained under the declared target condition?',
               change_criterion='A controlled contradiction would change the candidate choice.')


def prepare(tmp_path, *, enabled=True):
    public = tmp_path / 'public'; public.mkdir(parents=True)
    objective = public / 'objective.md'; objective.write_text('Compare mechanisms without abandoning required controls.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-launched'], max_tokens=100_000,
        max_seconds=3600, max_steps=16, model='fixture', reasoning_effort='high',
        decision_contract_version=1 if enabled else None,
        search_policy=None)
    finish(root, 1, 'H', [], action='experiment')
    finish(root, 2, 'W', [1], text='Negative controlled result; target uncertainty remains.')
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
    accepted_budget.accept_native(root,step,record,c.read(c.regular(record/'native-intent.json')))
    c.write(record / 'science-output.json', payload)
    c.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None, native_started=False, usage=native.usage(root),
                                             raw_ref=c.file_ref(record / 'science-output.json')))
    c.seal_result(root, step)
    return job, payload


def test_ordinary_a_cannot_author_a_binding_decision_request(tmp_path):
    root = prepare(tmp_path)
    with pytest.raises(OutputRepairRequired, match='decision_request=null'):
        author(root, 3, [1, 2], REQUEST)
    record = c.host_record(c.workspace(root, 3))
    assert c.read(record / 'science-output.json')['decision_request'] == REQUEST
    assert not (record / 'result-identity.json').exists()


def test_omitted_request_does_not_satisfy_explicit_advice_return(tmp_path):
    root = prepare(tmp_path)
    with pytest.raises(OutputRepairRequired, match='decision_request=null'):
        author(root, 3, [1, 2], None, omit_request=True)
    record = c.host_record(c.workspace(root, 3))
    assert 'decision_request' not in c.read(record / 'science-output.json')
    assert not (record / 'result-identity.json').exists()


def test_advice_returns_to_w_without_a_decision_request(tmp_path):
    root = prepare(tmp_path)
    job, payload = author(root, 3, [1, 2], None)
    assert decision.advice_returned(job, payload)
    assert decision.reason(root, job, payload) is None
    assert c.read(c.host_record(c.workspace(root, 3)) / 'result-identity.json')['control_outcome']['decision_status'] == 'applied'


def test_minimal_schema_and_current_tasks_stay_frozen_without_decision_contract(tmp_path):
    schema = c.result_schema('A')
    assert schema['additionalProperties'] is True
    assert set(schema['required']) == {'summary', 'scientific_result', 'limitations', 'next_action', 'route_reason'}
    assert 'decision_request' not in schema['properties']
    root = prepare(tmp_path, enabled=False)
    job, _ = author(root, 3, [1, 2], None)
    originals = {path:path.read_bytes() for path in (root/'settings.json', root/'jobs/003.json',
                 c.workspace(root,3)/'science-task.json', c.host_record(c.workspace(root,3))/'result-identity.json')}
    assert job['handoff_protocol'] == protocol.CURRENT and 'decision_contract_version' not in c.settings(root)
    assert c.build_job(root, 3, 'A', [1, 2]) == job
    assert all(path.read_bytes() == raw for path,raw in originals.items())
    assert c.build_job(root, 4, 'W', [1, 2, 3])['handoff_protocol'] == protocol.CURRENT
    assert all(path.read_bytes() == raw for path,raw in originals.items())


def test_retired_schema_and_task_writer_reject_without_changing_originals(tmp_path):
    retired = 10
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
