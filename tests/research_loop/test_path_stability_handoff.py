"""Exact comparison originals through real seals; authored science, no models.

The observer transport supplies tiny public fixture declarations. Production
capture, accepted candidate bindings, selections, task publication and fork cuts
remain real. These checks establish reachability, never a scientific winner.
"""
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import (
    accepted_prefix, candidate_refs, contracts as c, controller, fork_runtime,
    fork_service, handoff_state, history, native, originals, search,
    shared_input_views,
)
from test_direct_input_bindings import stopped_seals
from test_history_handoff import finish


DRIVER = 'def evaluate(predictions, targets):\n    return sum(abs(p-y) for p, y in zip(predictions, targets))\n'
REPORT = json.dumps(dict(evaluation_driver='evaluate.py', conditions='public-split-v1',
    metric='absolute_error_sum', direction='min', results=None,
    limitations=['The public evaluation dataset is missing; no fit or comparison was run.']))
FILES = {
    'evaluate.py': DRIVER,
    'protocol.json': REPORT,
    'retained-flow.json': '{"training":["fixed preprocessing","fit linear estimator"],"evaluation":"public-split-v1"}\n',
    'rival-flow.json': '{"training":["fixed preprocessing","fit tree estimator"],"evaluation":"public-split-v1"}\n',
}
SELECTED = ['W002-I001', 'W002-I002', 'W002-I003']
SELECTED_PATHS = {f'inputs/parents/002/artifacts/{name}'
    for name in ('evaluate.py', 'protocol.json', 'retained-flow.json')}
MISSING = 'The public evaluation dataset is missing; no scientific comparison is available.'


def plan(*, revised=False):
    return dict(scope='Compare complete authored training procedures under one public evaluation.',
        commitments=[dict(label='Fixture mechanism', existing_ref='h001.m1' if revised else None,
            minimal_test=dict(evaluation_driver='W002-I001' if revised else 'To be authored by W',
                conditions='public-split-v1', missing_input='public evaluation dataset'))],
        focus=dict(index=1, action='screen'))


def nominations(candidate):
    return dict(representatives=[dict(mechanism_ref='h001.m1', candidate=candidate,
        reason='Authored fixture preview only; no scientific ranking.')], extras=[], gaps=[])


def blocked():
    return [dict(mechanism='h001.m1', basis='blocked', finding=MISSING)]


def prepare(tmp_path, monkeypatch):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain exact public evaluation and complete training-procedure originals.\n')
    adapter = tmp_path / 'observer.py'
    adapter.write_text('# Inert fixture adapter; read_adapter is supplied at its transport boundary.\n')
    root = tmp_path / 'instance'
    future = {}

    def observed(config, request):
        assert config['config'] == {}
        candidates = []
        if request['step'] == 2:
            candidates = [dict(id=identity, label=identity,
                implementation=f'history/nodes/002/artifacts/{name}',
                evaluation_key='public-split-v1', metric=None, direction='min', status='unknown',
                evidence=['project/nodes/002/artifacts/protocol.json'])
                for identity, name in (('retained-process', 'retained-flow.json'),
                    ('rival-process', 'rival-flow.json'))]
        if (request['root'], request['step']) in future:
            step = request['step']
            candidates = [dict(id='future-process', label='Future source-only procedure',
                implementation=f'history/nodes/{step:03d}/artifacts/future-flow.json',
                evaluation_key='changed-public-v2', metric=None, direction='min', status='unknown',
                evidence=[f'project/nodes/{step:03d}/artifacts/future-flow.json'])]
        return dict(candidates=candidates, current_candidate=None, actual_submission=None,
            coverage=dict(status='complete', note='Exact fixture declarations; scientific effectiveness is unknown.'))

    monkeypatch.setattr(history, 'read_adapter', observed)
    controller.prepare(root, objective, public, ['fixture-never-launch'], max_tokens=100000,
        max_seconds=300, max_steps=40, model='fixture', reasoning_effort='high',
        experiment_mode='external', search_policy=search.DEFAULT_POLICY,
        history_observer=dict(adapter=str(adapter), config={}))
    stopped_seals(monkeypatch)
    finish(root, 1, 'H', [], action='experiment', search_choice=plan(), limitations=[MISSING])
    finish(root, 2, 'W', [1], files=FILES, mechanism_findings=blocked(),
        candidate_nominations=nominations('retained-process'), limitations=[MISSING])
    expected = {name: (c.workspace(root, 2) / 'artifacts' / name).read_bytes() for name in FILES}
    assert expected['retained-flow.json'] != expected['rival-flow.json']
    return root, expected, future


def build(root, step, role, parents, monkeypatch):
    def replay(*_args, **_kwargs):
        pytest.fail('Current exact-reference handoff replayed historical science')
    with monkeypatch.context() as patch:
        patch.setattr(history, 'source_sequence', replay)
        patch.setattr(history, 'rebuild', replay)
        return c.build_job(root, step, role, parents)


def assert_request(root, job, expected):
    """Follow the actual native request to its published task and original mounts."""
    work = c.workspace(root, job['step'])
    task = c.read(work / 'science-task.json')
    prompt, _, _ = native.render_scientific_request(job, task, {}, backend_name='codex',
        instruction_transport='prompt')
    assert 'science-task.json' in prompt
    catalogue = task['lineage']['candidates']
    assert catalogue['candidate_count'] == 2
    assert catalogue['index'] == task['lineage']['indexes']['candidates']
    assert catalogue['index']
    reused = {row['path'] for row in task['reused_implementations']}
    assert SELECTED_PATHS <= reused
    selected = {row['material']['path']: row for row in job['selected_inputs']}
    assert SELECTED_PATHS <= selected.keys()
    options = {row['id']: row['path'] for row in job['implementation_options']}
    assert {options[identity] for identity in SELECTED} == SELECTED_PATHS
    arguments = shared_input_views.mount_arguments(root, job, work, include_skills=False)
    for path in SELECTED_PATHS:
        material = selected[path]['material']
        original = originals.material_path(root, material)
        assert original == Path(material['source'])
        assert original.read_bytes() == expected[Path(path).name]
        assert not (work / path).exists()
        assert any(arguments[index:index + 3] == ['--ro-bind', str(original), str(work / path)]
            for index in range(len(arguments) - 2))
    assert json.loads(originals.material_path(root, selected[
        'inputs/parents/002/artifacts/protocol.json']['material']).read_text())['results'] is None
    assert 'sha256' not in json.dumps(task)
    # Full candidate availability and selected evaluation-driver reachability
    # are independent facts. Neither establishes an automatic scientific choice.
    for identity, implementation in [('retained-process', 'W002-I003'), ('rival-process', 'W002-I004')]:
        assert candidate_refs.is_available(root, job, identity)
        binding, = history.candidate_materials(root, job, candidate_id=identity)
        assert binding['implementation_id'] == implementation
        assert originals.material_path(root, binding['material']).read_bytes() == expected[
            Path(binding['material']['path']).name]
    return task


@pytest.mark.parametrize('extra_seals', [0, 12])
def test_selected_comparison_originals_survive_h_revision_latest_preview_and_long_prefix(
        tmp_path, monkeypatch, extra_seals):
    root, expected, _ = prepare(tmp_path, monkeypatch)
    with handoff_state.scope(root):
        finish(root, 3, 'A', [1, 2], action='revise_h', selection=SELECTED,
            recommendation='retained-process', limitations=[MISSING])
        hjob = build(root, 4, 'H', [1, 2, 3], monkeypatch)
        assert_request(root, hjob, expected)
        finish(root, 4, 'H', [1, 2, 3], action='experiment', search_choice=plan(revised=True),
            limitations=[MISSING])
        wjob = build(root, 5, 'W', [4], monkeypatch)
        assert_request(root, wjob, expected)
        finish(root, 5, 'W', [4], selection=SELECTED,
            files={'latest-report.json': '{"comparison":"still blocked by missing public dataset"}\n'},
            mechanism_findings=blocked(), candidate_nominations=nominations('rival-process'),
            limitations=[MISSING])
        ajob = build(root, 6, 'A', [4, 5], monkeypatch)
        assert_request(root, ajob, expected)
        preview = c.read(accepted_prefix.visibility(root, ajob['history']['prefix']) / 'breadth-table.json')['nominations']
        assert [row['candidate_id'] for row in preview['representatives']] == ['rival-process']
        finish(root, 6, 'A', [4, 5], action='revise_h', selection=SELECTED,
            recommendation='retained-process', limitations=[MISSING])
        for step in range(7, 8 + extra_seals):
            build(root, step, 'H', [step - 1], monkeypatch)
            finish(root, step, 'H', [step - 1], action='experiment',
                search_choice=plan(revised=True), limitations=[MISSING])
        following = build(root, 8 + extra_seals, 'W', [7 + extra_seals], monkeypatch)
        assert following['history']['prefix']['count'] == 7 + extra_seals
        assert_request(root, following, expected)
    seal = c.read(c.host_record(c.workspace(root, 6)) / 'result-identity.json')
    assert seal['control_outcome']['diagnostics'] == []
    assert c.read(c.host_record(c.workspace(root, 6)) / 'science-output.json')['candidate_recommendation'] == 'retained-process'


def test_fork_exact_cut_keeps_selected_driver_and_both_candidates_without_future_source_choice(tmp_path, monkeypatch):
    root, expected, future = prepare(tmp_path, monkeypatch)
    finish(root, 3, 'A', [1, 2], action='revise_h', selection=SELECTED,
        recommendation='retained-process', limitations=[MISSING])
    source, _, _ = finish(root, 4, 'H', [1, 2, 3], action='experiment',
        search_choice=plan(revised=True), limitations=[MISSING])
    future[str(root), 5] = True
    finish(root, 5, 'W', [4], files={'future-flow.json': '{"training":"source future only"}\n'},
        mechanism_findings=blocked(), candidate_nominations=nominations('rival-process'), limitations=[MISSING])
    latest, _, _ = finish(root, 6, 'A', [4, 5], action='revise_h',
        recommendation='future-process', limitations=[MISSING])
    assert candidate_refs.is_available(root, latest, 'future-process')
    for step in range(1, 7):
        (c.host_record(c.workspace(root, step)) / 'runner.lock').touch(exist_ok=True)
    with controller._writer(root):
        pass
    child = tmp_path / 'child'
    limits = dict(max_tokens=10000, max_seconds=60, max_steps=4)
    preview = fork_service.preview(root, 4, child, budget=limits)
    fork_service.create(root, 4, child, budget=limits,
        expected_preview_id=preview['preview_id'], operation_id='path-stability-exact-cut')
    selected = fork_runtime.build_selected_job(child, 4)
    assert selected['direct_materials'] == source['direct_materials']
    assert selected['history']['prefix']['cutoff'] == 3
    assert_request(child, selected, expected)
    assert not candidate_refs.is_available(child, selected, 'future-process')
    finish(child, 4, 'H', [1, 2, 3], action='experiment', search_choice=plan(revised=True), limitations=[MISSING])
    wjob = build(child, 5, 'W', [4], monkeypatch)
    assert_request(child, wjob, expected)
    finish(child, 5, 'W', [4], selection=SELECTED, mechanism_findings=blocked(),
        candidate_nominations=nominations('rival-process'), limitations=[MISSING])
    ajob = build(child, 6, 'A', [4, 5], monkeypatch)
    assert_request(child, ajob, expected)
    assert not candidate_refs.is_available(child, ajob, 'future-process')


def test_all_candidates_rejected_and_missing_evaluation_input_allow_finite_final_response(tmp_path, monkeypatch):
    root, _, _ = prepare(tmp_path, monkeypatch)
    job = build(root, 3, 'A', [1, 2], monkeypatch)
    assert not job.get('selected_inputs')  # Availability never selects a default comparator.
    _, work, record = finish(root, 3, 'A', [1, 2], action='finish', selection=[],
        recommendation=None, text='Both complete procedures are rejected for this bounded result; ' + MISSING,
        limitations=[MISSING])
    seal = c.read(record / 'result-identity.json')
    assert seal.get('selected_inputs', []) == [] and seal['relayed_material_refs'] == []
    assert c.read(record / 'science-output.json')['limitations'] == [MISSING]
    prompt, _, _ = native.render_scientific_request(job, c.read(work / 'science-task.json'), {},
        backend_name='codex', instruction_transport='prompt', final_return=True)
    assert 'Protected FINAL: conclude now; no new role/audit/experiment.' in prompt
    assert native.STAGE_PLANNING['A'] not in prompt
    assert not (root / 'jobs/004.json').exists()
