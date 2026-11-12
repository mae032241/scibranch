"""Bounded portfolio navigation and real calls without A planning authority."""
import copy
import json

import pytest

from scidiscovery.research_loop import accepted_budget, accepted_prefix, contracts as c, controller, execution, history
from scidiscovery.research_loop.protocol import CURRENT, UnsupportedProtocol
from execution_fixtures import execution_registry
from test_history_handoff import finish

P, Q, R = 'h001.m1', 'h001.m2', 'h001.m3'


def prepare(tmp_path, *, enabled=True):
    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'objective.md'; objective.write_text('Retain the original mechanism portfolio and its limitations.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launched'], max_tokens=100_000,
        max_seconds=3600, max_steps=64, model='fixture', reasoning_effort='high',
        portfolio_policy=dict(review_after_cycles=2) if enabled else None)
    finish(root, 1, 'H', [], action='experiment', search_choice=dict(commitments=[dict(label=label)
        for label in ('P', 'Q', 'R', 'S')], focus=dict(index=None, action='screen')))
    return root


def mechanism_rows(root, step):
    job = c.read(root / 'jobs' / f'{step:03d}.json')
    public = accepted_prefix.visibility(root, accepted_prefix.for_job(job))
    rows = c.read(public / 'breadth-table.json')['mechanisms']
    snapshot = root / job['history']['breadth_snapshot']['path']
    assert snapshot.stat().st_mode & 0o222 == 0
    frozen = c.read(snapshot)['table']['mechanisms']
    assert {row['mechanism_ref'] for row in rows} == set(frozen)
    for row in rows:
        assert all(row[key] == frozen[row['mechanism_ref']][key]
            for key in ('source_step', 'source_role', 'origin_ref'))
    return rows, public


def test_many_mechanisms_bounded_navigation_and_exact_source_tamper(tmp_path):
    root = prepare(tmp_path)
    # H deltas add 16 mechanisms; compact navigation retains every original.
    for step in range(2, 6):
        finish(root, step, 'H', [step - 1], action='analyze', search_choice=dict(
            commitments=[dict(label='机' * 70 + f'{step}-{i}', minimal_test='测' * 150) for i in range(4)] +
                [dict(label='P', existing_ref=P)], focus=dict(index=5, action='screen')))
    job = c.build_job(root, 6, 'A', [5])
    view = c.science_view(job, root)['lineage']
    assert len((json.dumps(view, ensure_ascii=False, indent=2) + '\n').encode()) <= history.MAX_BRIEF_BYTES
    assert view['mechanisms']['count'] == 20 and view['mechanisms']['availability'] == 'available'
    rows, public = mechanism_rows(root, 6)
    assert len(rows) == 20 and len({row['mechanism_ref'] for row in rows}) == 20
    by_ref = {row['mechanism_ref']: row for row in rows}
    assert by_ref[P]['source_step'] == 5 and by_ref[Q]['source_step'] == 1
    page = view['indexes']['mechanisms']; steps = []
    while page:
        path = public / page.removeprefix('inputs/history/')
        assert path.stat().st_mode & 0o222 == 0
        assert path.stat().st_size <= history.MAX_PAGE_BYTES
        node = c.read(path); steps.append(node['step'])
        original = path.parent / node['scientific_original']
        assert original.stat().st_mode & 0o222 == 0
        assert original.samefile(execution.record(root, node['step']) / 'science-output.json')
        page = node['previous']
    assert steps == [5, 4, 3, 2, 1]
    c._validate_inputs(root, c.workspace(root, 6), job)
    # Closed navigation is advisory; the direct H5 original is a current input.
    bound = public / by_ref[P]['original'].removeprefix('inputs/history/')
    bound.chmod(0o600); bound.write_text('{}\n')
    with pytest.raises(ValueError):
        c._validate_inputs(root, c.workspace(root, 6), job)


def test_default_portfolio_preserves_frozen_tasks_without_extra_report_field(tmp_path):
    root = prepare(tmp_path, enabled=False)
    finish(root, 2, 'W', [1])
    job, _, record = finish(root, 3, 'A', [1, 2], portfolio_review=None, final_coverage=None)
    assert job['handoff_protocol'] == CURRENT and 'portfolio_review' not in c.result_schema('A')['properties']
    paths = [root / 'settings.json', root / 'jobs/003.json', c.workspace(root, 3) / 'science-task.json',
        record / 'science-result.schema.json', record / 'result-identity.json']
    original = {path: path.read_bytes() for path in paths}
    assert c.build_job(root, 3, 'A', [1, 2], instruction=job['instruction']) == job
    assert all(path.read_bytes() == raw for path, raw in original.items())
    assert c.build_job(root, 4, 'H', [3])['handoff_protocol'] == CURRENT
    with pytest.raises(UnsupportedProtocol):
        c.result_schema('A', 10)
    assert 'portfolio_policy' not in c.settings(root, job)
    assert 'portfolio_review' not in job['scientific_response_fields']
    assert 'portfolio_review' not in c.result_schema('A', final_only=True)['properties']
    assert len(mechanism_rows(root, 3)[0]) == 4


@pytest.mark.parametrize('bad', [None, 0])
def test_portfolio_requires_model_visible_unified_layout(tmp_path, bad):
    public = tmp_path / 'public'; public.mkdir(); objective = public / 'objective'; objective.write_text('Original goal')
    with pytest.raises(ValueError, match='unified lineage'):
        controller.prepare(tmp_path / 'instance', objective, public, ['never-launched'], max_tokens=1000, max_seconds=60,
            max_steps=8, model='fixture', reasoning_effort='high', lineage_projection_version=bad,
            portfolio_policy=dict(review_after_cycles=3))
    assert not (tmp_path / 'instance').exists()


@pytest.mark.process_e2e
def test_three_w_real_calls_keep_exact_sources_and_optional_advice_without_work_orders(tmp_path, monkeypatch, execution_registry):
    from pathlib import Path
    from breadth_execution_fixtures import _launch
    from test_breadth_science import prepare as breadth_prepare, h, worker, findings
    from scidiscovery.research_loop import experiments, native, worker_execution_bridge as bridge, worker_research
    root = breadth_prepare(tmp_path, qualified_local=True, portfolio=True)
    h(root, labels=('P', 'Q', 'R'))
    originals = {}
    review = dict(main_line=P, reason='Optional scientific opinion; missing target evidence remains missing.',
        discriminating_work=dict(mechanism_refs=[Q, R], question='Which target condition distinguishes Q/R?', next_action='extract'))
    coverage = [dict(mechanism_ref=ref, status='deep' if ref == P else 'screened_pending',
        reason='Authored fixture opinion, without scientific qualification.') for ref in (P, Q, R)]
    worker_refs = ('w002.P', 'w004.Q', 'w006.R')
    for step, parents, mechanism in ((2, [1], worker_refs[0]), (4, [1, 2, 3], worker_refs[1]), (6, [1, 4, 5], worker_refs[2])):
        task, config = worker(root, tmp_path, step, parents, qualified_local=True)
        work = c.workspace(root, step)
        script = work / 'code/mechanical.py'; script.parent.mkdir(exist_ok=True)
        script.write_text('print("mechanical fixture only")\n')
        question = dict(question='Does this bounded mechanical command complete?',
            change_criterion='A failed command changes the implementation diagnosis, without scientific qualification.')
        research_file = work / 'work/research.json'
        c.write(research_file, dict(declarations=[dict(mechanism_ref=mechanism, action='declare',
            question='An explicit W-owned mechanical research direction', exploration=True)],
            mechanism_ref=mechanism, experiment_question=question))
        runtime = work / 'experiments/breadth-fixture'; runtime.mkdir(parents=True, exist_ok=True)
        launch = _launch(root, step, config['native_attempt']['number'])
        with bridge.serve(work / bridge.SUBMISSION_DIRECTORY, work / 'work/experiment-config.json', work, launch=launch) as endpoint:
            reply = bridge.client(endpoint, ['run', '--script', str(script), '--new-plan',
                '--research', str(research_file), '--experiment-dir', str(runtime)])
        assert reply['code'] == 0 and reply['result']['status'] == 'known_success', reply
        accepted = experiments.Records(config).accepted(reply['result']['call_id'])
        binding = worker_research.check_binding(accepted['mechanism_binding'], config['scope'])
        assert binding['mechanism_ref'] == mechanism and binding['source_step'] == step and binding['source_role'] == 'W'
        assert binding['research_binding']['experiment_question'] == question
        declaration = worker_research.load_original(binding, config['scope'])
        assert declaration['value']['mechanisms'][mechanism]['source_step'] == step
        investment = worker_research.investment(config['scope'], [accepted], research_state=worker_research.load(config))
        assert investment['total_calls'] == 1 and investment['by_mechanism'] == {mechanism: 1}
        for path in (Path(binding['source_ref']['path']), Path(config['record_dir']) / 'calls' / accepted['call_id'] / 'accepted.json'):
            originals[path] = path.read_bytes()
        intent = execution.record(root, step) / 'native-intent.json'
        originals[intent] = intent.read_bytes()
        # H suggestions remain separate from the newly declared W call owner.
        blocked = [row['mechanism_ref'] for row in task['search']['coverage_obligation']]
        finish(root, step, 'W', parents, mechanism_findings=findings(blocked=blocked), preserve_native_intent=True)
        for name in ('science-output.json', 'result-identity.json'):
            path = execution.record(root, step) / name
            originals[path] = path.read_bytes()
        before = copy.deepcopy(accepted_budget.read(root)['search'])
        advice = copy.deepcopy(review)
        if step > 2:
            advice.update(discriminating_work=None, work_updates=[dict(work_ref='A003-D001', action='withdraw',
                reason='An optional update creates no accepted work order.')])
        author, _, record = finish(root, step + 1, 'A', [1, step], action='continue_w',
            search_choice=dict(dispositions=[dict(mechanism_ref=Q, status='waived', reason='Advice only')],
                next_focus=dict(mechanism_ref=Q, action='improve')),
            portfolio_review=advice, final_coverage=coverage)
        assert author['advisory_scope_version'] == 1 and author['research_strategy_version'] == 3
        originals[record / 'science-output.json'] = (record / 'science-output.json').read_bytes()
        assert c.validate(c.workspace(root, step + 1))['portfolio_review'] == advice
        assert c.validate(c.workspace(root, step + 1))['final_coverage'] == coverage
        after = accepted_budget.read(root)['search']
        for field in ('focus', 'allocation', 'table', 'extra_grant', 'last_science', 'portfolio'):
            assert after[field] == before[field]
        assert after['work_count'] == 0 and after['pending_work_response'] is None
        assert after['portfolio']['completed_cycle_count'] == 0
        assert after['portfolio']['main_line'] is None and after['portfolio']['mainline_source'] is None
        assert c.seal_result(root, step + 1)['portfolio_review'] == advice
    counts = {ref: row['accepted_calls'] for ref, row in after['table']['mechanisms'].items()}
    assert counts == {P: 0, Q: 0, R: 0, **{ref: 1 for ref in worker_refs}}
    assert after['portfolio']['pending_w']['steps'] == [2, 4, 6]
    assert set(after['portfolio']['pending_w']['all_branches']) == set(worker_refs)
    launched = []
    def execute(root, step):
        job = c.read(root / 'jobs' / f'{step:03d}.json')
        assert step == 8 and job['role'] == 'W' and 'author_correction' not in job
        assert job['search']['focus'] == after['focus']
        launched.append(step)
        c.write(root / 'pause.request', dict(reason='Stop after one accepted W handoff'))
        parents = [int(row['path'].split('/')[2]) for row in job['direct_refs']]
        finish(root, step, 'W', parents, stage_intent='handoff',
            mechanism_findings=findings(blocked=[row['mechanism_ref'] for row in job['search']['coverage_obligation']]))
    monkeypatch.setattr(native, 'execute_native', execute)
    assert controller.run(root)['status'] == 'paused' and launched == [8]
    assert controller.run(root)['status'] == 'paused' and launched == [8]
    assert all(path.read_bytes() == raw for path, raw in originals.items())
