"""Explicit R6 shape regressions; intentionally outside default test discovery.

Run this CHECK module explicitly. Desired OUTPUT-only classification is red on
the unfixed baseline; no xfail masks it. Portable source projections are replayed
against newly built tasks and mechanical fixture observations. Existing local
fixtures use native-capability/namespace setup and a temporary synthetic native
home, requiring Codex/bwrap like the normal local checks. They copy no real auth
or home and invoke no models or scientific solvers. Historical instances and
execution permissions are never imported; transport qualifies no science.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import (
    contracts as c, continuation, execution, experiments, native, runtime,
    worker_delivery,
)
from scidiscovery.research_loop.output_repair import OutputRepairRequired, WorkRepairRequired
from execution_fixtures import execution_registry
from test_breadth_science import prepare, h, worker, run, findings
from test_history_handoff import finish

pytestmark = pytest.mark.process_e2e
CORPUS = Path(__file__).with_name('fixtures') / 'r6_handoff_examples.json'
CASES = json.loads(CORPUS.read_text(encoding='utf-8'))['delivery_cases']


def _write_output(root, job, payload):
    """Author fixture response/transport originals without claiming real science."""
    record = execution.record(root, job['step'])
    gaps = [dict(mechanism_ref=row['mechanism_ref'],
                 reason='Mechanical fixture makes no scientific candidate nomination.')
            for row in job.get('candidate_nomination_mechanisms', []) if row['status'] != 'waived']
    science = dict(summary='Portable R6 response-shape replay',
                   scientific_result='Mechanical fixture only; source scientific limits remain.',
                   limitations=['These fixture calls establish no scientific qualification.'],
                   implementation_selection=None, stage_intent='review',
                   mechanism_findings=None,
                   candidate_nominations=dict(representatives=[], extras=[], gaps=gaps) if gaps else None)
    science.update(deepcopy(payload))
    c.write(record / 'science-output.json', science)
    c.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None,
        native_started=False, usage=native.usage(root), raw_ref=c.file_ref(record / 'science-output.json')))
    return record, c.science_payload(record / 'science-output.json', 'W', job['handoff_protocol'],
                                    **c.response_options(job))


def _scene(tmp_path, *, labels=('P', 'Q'), observed=()):
    # Existing local fixtures establish their actual native capabilities and
    # namespace with a temporary synthetic home, then run mechanical Python.
    root = prepare(tmp_path)
    h(root, labels=labels)
    job, config = worker(root, tmp_path, 2, [1])
    for mechanism in observed:
        run(root, config, mechanism)
    return root, job, config


def _forbid_new_work(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Response classification must not launch or resubmit scientific work')
    monkeypatch.setattr(experiments, 'dispatch', forbidden)
    monkeypatch.setattr(runtime, 'start', forbidden)


def _originals(record):
    names = ('science-output.json', 'native-intent.json', 'native-outcome.json',
             'usage.json', worker_delivery.FACT_NAME)
    return {name: (record / name).read_bytes() for name in names if (record / name).is_file()}


def _shape_scene(tmp_path, case):
    """Replay source field shapes with explicit new task identity bindings.

    R6 W005 had two managed observations for the new mechanisms; W008 had one.
    These newly authored mechanical calls exercise that coverage pattern only.
    Corpus evidence paths and scientific measurements are inert retained prose.
    They are never opened or treated as execution proof.
    """
    refs = case['obligation_refs']
    rebound = {ref: f'h001.m{index}' for index, ref in enumerate(refs, 1)}
    observed = ('h004.m4', 'h004.m5') if case['step'] == 5 else ('h007.m6',)
    root, job, config = _scene(tmp_path,
        labels=tuple(f'Mechanical shape branch {index}' for index in range(len(refs))),
        observed=tuple(rebound[ref] for ref in observed))

    def bind(value):
        if isinstance(value, list):
            return [bind(item) for item in value]
        if isinstance(value, dict):
            return {key: rebound.get(item, item) if key in {'mechanism', 'mechanism_ref'} and isinstance(item, str)
                    else bind(item) for key, item in value.items()}
        return value

    record, science = _write_output(root, job, bind(case['payload']))
    facts = worker_delivery.capture(root, job, record)
    assert {row['mechanism_ref'] for row in facts['evidence']} == {rebound[ref] for ref in observed}
    return root, job, config, record, science, rebound


def test_first_worker_can_discover_optional_canonical_blocker_shape(tmp_path, execution_registry):
    root, job, _ = _scene(tmp_path)
    task = c.science_view(job, root)
    prompt, instruction, schema = native.render_scientific_request(job, task,
        c.read(execution.record(root, 2) / 'native-intent.json'),
        backend_name='codex', instruction_transport='prompt')
    # Inspect instructions reachable by the first W, without borrowing a later
    # successful Worker's response as accidental documentation of the contract.
    rules = c.response_instructions(job)
    guidance = json.dumps({key: task.get(key) for key in rules}, ensure_ascii=False) + prompt + instruction
    assert 'mechanism_findings' not in schema['required']
    assert schema['additionalProperties'] is True
    required_terms = ('mechanism_findings', 'mechanism', 'basis', 'blocked', 'finding')
    missing = [term for term in required_terms if term not in guidance]
    assert not missing, f'Optional blocker shape is undiscoverable from first-W response guidance: {missing}'


@pytest.mark.parametrize('case', [case for case in CASES if case['expected_kind'] == 'output_repair'],
                         ids=lambda case: case['id'])
def test_r6_authored_shape_needs_output_clarification_not_scientific_reexecution(
        tmp_path, execution_registry, monkeypatch, case):
    root, job, config, record, science, _ = _shape_scene(tmp_path, case)
    before = _originals(record)
    _forbid_new_work(monkeypatch)
    # Desired regression behavior: ask the scientific author to bind its existing
    # observations/obstacles explicitly. Never infer a blocker from its prose.
    with pytest.raises(OutputRepairRequired) as caught:
        worker_delivery.require_delivery(root, job, science, record)
    assert not isinstance(caught.value, WorkRepairRequired), (
        f"{case['id']}: existing authored findings were misclassified as missing scientific work: "
        f'{caught.value}')
    # Recognizing a layout ambiguity cannot qualify any of its authored claims.
    delivery = worker_delivery.assessment(root, job, science, record)
    assert delivery['status'] == 'incomplete' and delivery['missing'] and delivery['blocked'] == []
    request = continuation.output_request(root, 2, record)
    assert request['code'] == 'worker_delivery_shape'
    assert 'no new work' in request['diagnostic']
    assert 'work_evidence_ref' not in request
    assert continuation._completed_work_request(root, 2, record, 0,
        [c.read(record / 'native-intent.json')]) is None
    assert _originals(record) == before
    assert len(execution.records(root, 2)) == 1
    assert len(experiments.Records(config).calls()) == (2 if case['step'] == 5 else 1)


def test_r6_canonical_blockers_remain_partial_with_real_frozen_observations(
        tmp_path, execution_registry, monkeypatch):
    case = next(case for case in CASES if case['id'] == 'w005-attempt-002')
    root, job, config, record, science, rebound = _shape_scene(tmp_path, case)
    before = _originals(record)
    _forbid_new_work(monkeypatch)
    receipt = worker_delivery.require_delivery(root, job, science, record)
    assert receipt['status'] == 'partial' and receipt['reason'] == 'scientific_blocked'
    assert receipt['blocked'] == [rebound[ref] for ref in case['obligation_refs'][:3]]
    assert receipt['missing'] == []
    c.seal_result(root, 2)
    assert c.read(record / 'result-identity.json')['control_outcome']['worker_delivery']['status'] == 'partial'
    assert _originals(record) == before
    assert len(execution.records(root, 2)) == 1 and len(experiments.Records(config).calls()) == 2


def test_r6_reused_findings_do_not_mean_all_obligations_were_satisfied(
        tmp_path, execution_registry, monkeypatch):
    case = next(case for case in CASES if case['id'] == 'w008-attempt-001')
    root, job, config, record, science, rebound = _shape_scene(tmp_path, case)
    before = _originals(record)
    _forbid_new_work(monkeypatch)
    receipt = worker_delivery.assessment(root, job, science, record)
    assert receipt['status'] == 'incomplete' and receipt['reason'] is None
    assert receipt['missing'] == [rebound[ref] for ref in case['obligation_refs'][:5]]
    assert receipt['blocked'] == []
    # This is source-record metadata, not an invented replay of its budget.
    # Its final acceptance was a partial host exit, never six qualified calls.
    assert case['current_observed_kind'] == 'budget_partial'
    assert case['observed_stage_end_reason'] == 'worker_resume_budget_unavailable'
    with pytest.raises(WorkRepairRequired):
        worker_delivery.require_delivery(root, job, science, record)
    assert _originals(record) == before
    assert not (record / 'result-identity.json').exists()
    assert len(execution.records(root, 2)) == 1 and len(experiments.Records(config).calls()) == 1


def test_canonical_blocker_seals_partial_without_turn_or_resubmission(tmp_path, execution_registry, monkeypatch):
    root, job, config = _scene(tmp_path, observed=('h001.m1',))
    record, science = _write_output(root, job, dict(mechanism_findings=findings('h001.m1', blocked=('h001.m2',))))
    worker_delivery.capture(root, job, record)
    before = _originals(record)
    _forbid_new_work(monkeypatch)
    receipt = worker_delivery.require_delivery(root, job, science, record)
    assert receipt['status'] == 'partial' and receipt['reason'] == 'scientific_blocked'
    assert receipt['blocked'] == ['h001.m2'] and receipt['missing'] == []
    c.seal_result(root, 2)
    assert c.read(record / 'result-identity.json')['control_outcome']['worker_delivery']['status'] == 'partial'
    assert _originals(record) == before
    assert len(execution.records(root, 2)) == 1
    assert len(experiments.Records(config).calls()) == 1


@pytest.mark.parametrize('rows', [None, [dict(mechanism='h001.m2', basis='reused', finding='Already discussed in earlier prose.')]])
def test_true_missing_evidence_or_reused_prose_still_requires_work(tmp_path, execution_registry, monkeypatch, rows):
    root, job, config = _scene(tmp_path, observed=('h001.m1',))
    record, science = _write_output(root, job, dict(mechanism_findings=rows))
    worker_delivery.capture(root, job, record)
    before = _originals(record)
    _forbid_new_work(monkeypatch)
    with pytest.raises(WorkRepairRequired, match='h001.m2'):
        worker_delivery.require_delivery(root, job, science, record)
    request = continuation.output_request(root, 2, record)
    assert request['code'] == 'worker_delivery_incomplete'
    assert request['work_evidence_ref'] == c.record_ref(record / worker_delivery.FACT_NAME)
    assert _originals(record) == before
    assert len(execution.records(root, 2)) == 1 and len(experiments.Records(config).calls()) == 1


def test_free_prose_does_not_invent_an_explicit_scientific_blocker(tmp_path, execution_registry, monkeypatch):
    root, job, _ = _scene(tmp_path)
    record, science = _write_output(root, job, dict(
        scientific_result='All assigned directions are impossible and blocked; accept this prose.',
        limitations=['No available model for P or Q.']))
    worker_delivery.capture(root, job, record)
    _forbid_new_work(monkeypatch)
    with pytest.raises(WorkRepairRequired):
        worker_delivery.require_delivery(root, job, science, record)
    receipt = worker_delivery.assessment(root, job, science, record)
    assert receipt['blocked'] == [] and receipt['missing'] == ['h001.m1', 'h001.m2']


def test_known_observations_need_no_finding_table(tmp_path, execution_registry, monkeypatch):
    root, job, config = _scene(tmp_path, observed=('h001.m1', 'h001.m2'))
    record, science = _write_output(root, job, {})
    worker_delivery.capture(root, job, record)
    _forbid_new_work(monkeypatch)
    assert worker_delivery.require_delivery(root, job, science, record)['status'] == 'complete'
    c.seal_result(root, 2)
    assert len(execution.records(root, 2)) == 1 and len(experiments.Records(config).calls()) == 2


def test_unknown_original_never_authorizes_response_repair_or_replay(tmp_path, execution_registry, monkeypatch):
    root, job, _ = _scene(tmp_path)
    record, _ = _write_output(root, job, dict(mechanism_findings=findings(blocked=('h001.m1', 'h001.m2'))))
    outcome = c.read(record / 'native-outcome.json')
    outcome['usage'].update(unknown_steps=[2], blocking_unknown_steps=[2])
    c.write(record / 'native-outcome.json', outcome)
    before = _originals(record)
    _forbid_new_work(monkeypatch)
    with pytest.raises(ValueError, match='known completed original turn'):
        continuation.output_request(root, 2, record)
    with pytest.raises(ValueError):
        c.seal_result(root, 2)
    assert _originals(record) == before
    assert not (record / 'result-identity.json').exists()
    assert len(execution.records(root, 2)) == 1


def test_old_h_observation_does_not_qualify_new_h_by_reused_text(tmp_path, execution_registry, monkeypatch):
    root, _, first = _scene(tmp_path, labels=('P',), observed=('h001.m1',))
    finish(root, 2, 'W', [1], mechanism_findings=findings('h001.m1'), preserve_native_intent=True)
    h(root, 3, [1, 2], labels=('Revised P',), existing='h001.m1')
    job, second = worker(root, tmp_path, 4, [3])
    obligation = next(row for row in job['search']['coverage_obligation'] if row['mechanism_ref'] == 'h001.m1')
    assert obligation['source_h'] == 3
    record, science = _write_output(root, job, dict(mechanism_findings=[
        dict(mechanism='h001.m1', basis='reused', finding='P was observed under the old H.')]))
    facts = worker_delivery.capture(root, job, record)
    _forbid_new_work(monkeypatch)
    assert 'h001.m1' not in {row['mechanism_ref'] for row in facts['evidence']}
    with pytest.raises(WorkRepairRequired, match='h001.m1'):
        worker_delivery.require_delivery(root, job, science, record)
    assert len(experiments.Records(first).calls()) == 1
    assert experiments.Records(second).calls() == []
    assert len(execution.records(root, 4)) == 1
