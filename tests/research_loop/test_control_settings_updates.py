"""Saved exploration controls affect real new-node gates, views and validation."""
import copy
import json

import pytest

from scidiscovery.research_loop import (candidate_nominations as nominations,
    configuration_form as form, configuration_updates as updates, contracts as c,
    controller, correction, execution, experiments, native, search)
from execution_fixtures import execution_registry
from test_configuration_form import Controls
from test_correction import worker as correction_worker
from test_portfolio_review import (P, Q, author, prepare as portfolio_instance,
    worker as portfolio_worker)


def save_controls(root, changes):
    """Submit the successful controls rendered by the actual edit form."""
    state = updates.status(root)
    draft = form.imported(state['configuration'], base=root)
    fields = Controls(form.render(draft, edit=True)).fields
    for path, value in changes.items():
        fields['cfg.' + path] = str(value)
    parsed = form.parse_edit(fields, base=root, configuration=state['configuration'])
    saved = updates.submit(root, parsed['document'], expected_revision=state['revision'])
    assert saved['status'] == 'configuration_saved' and saved['pending']
    return saved


def correction_instance(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Compare mechanisms and retain missing observations.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-launched'], max_tokens=100_000,
        max_seconds=3600, max_steps=40, model='fixture', reasoning_effort='high',
        correction_policy=correction.DEFAULT_POLICY)
    return root


@pytest.mark.parametrize('review_after,pause_after,verdict', [(1, 1, 'no_progress'), (2, 3, 'unknown')])
def test_form_saved_correction_thresholds_change_exact_controller_review_and_pause(
        tmp_path, monkeypatch, review_after, pause_after, verdict):
    root = correction_instance(tmp_path)
    old = c.build_job(root, 1, 'H', [])
    originals = {path: path.read_bytes() for path in (root / 'settings.json', root / 'jobs/001.json')}
    save_controls(root, {'budget.correction.review_after': review_after,
                         'budget.correction.pause_after': pause_after})
    decisions = {step: dict(verdict=verdict) for step in range(1, 40)}
    calls = correction_worker(monkeypatch, decisions=decisions)
    result = controller.run(root)
    assert result['status'] == 'correction_paused', result
    review_step = 2 * review_after + 1
    pause_step = 2 * (review_after + pause_after) + 2
    assert result['completed_steps'] == pause_step == len(calls)
    state = correction.project(root)
    assert [(row['level'], row['step']) for row in state['triggers']] == [
        ('review', review_step), ('pause', pause_step)]
    assert state['counters'][verdict] == review_after + pause_after
    assert state['current_pause']['reason'] == correction.REASONS[verdict]
    assert correction.project(root, review_step)['review_request'] is None
    assert correction.project(root, pause_step)['current_pause'] is None
    response = c.read(root / 'jobs' / f'{review_step + 1:03d}.json')
    assert response['role'] == 'H'
    assert response['correction_request']['trigger_id'] == state['triggers'][0]['trigger_id']
    new = c.read(root / 'jobs/003.json')
    assert c.settings(root, new)['correction_policy'] == dict(review_after=review_after, pause_after=pause_after)
    assert c.settings(root, old)['correction_policy'] == correction.DEFAULT_POLICY
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert controller.run(root)['correction_trigger']['trigger_id'] == state['current_pause']['trigger_id']
    assert len(calls) == pause_step and not (root / 'jobs' / f'{pause_step + 1:03d}.json').exists()


def test_lowering_correction_thresholds_does_not_reinterpret_sealed_old_assessment(tmp_path, monkeypatch):
    root = correction_instance(tmp_path)
    calls = correction_worker(monkeypatch)
    execute = native.execute_native

    def pause_after_first_assessment(root, step):
        execute(root, step)
        if step == 3:
            c.write(root / 'pause.request', dict(reason='Edit controls after an exact old assessment'))

    monkeypatch.setattr(native, 'execute_native', pause_after_first_assessment)
    assert controller.run(root)['status'] == 'paused'
    old = c.read(root / 'jobs/003.json')
    originals = {path: path.read_bytes() for path in (root / 'jobs/003.json',
        execution.record(root, 3) / 'science-output.json', execution.record(root, 3) / 'result-identity.json')}
    save_controls(root, {'budget.correction.review_after': 1, 'budget.correction.pause_after': 1})
    assert correction.project(root)['review_request'] is None
    assert c.settings(root, old)['correction_policy'] == correction.DEFAULT_POLICY
    (root / 'pause.request').unlink()
    monkeypatch.setattr(native, 'execute_native', execute)
    result = controller.run(root)
    assert result['status'] == 'correction_paused' and result['completed_steps'] == 8
    assert [(row['level'], row['step']) for row in correction.project(root)['triggers']] == [
        ('review', 5), ('pause', 8)]
    assert calls == [(1, 'H'), (2, 'W'), (3, 'A'), (4, 'W'), (5, 'A'), (6, 'H'), (7, 'W'), (8, 'A')]
    assert all(path.read_bytes() == raw for path, raw in originals.items())


def test_form_saved_portfolio_frequency_changes_new_view_at_completed_cycle_boundary(tmp_path, execution_registry):
    root = portfolio_instance(tmp_path, threshold=3)
    old = c.build_job(root, 3, 'A', [1, 2], instruction=controller._route(
        root, 2, 'W', c.validate(c.workspace(root, 2)), c.read(root / 'jobs/002.json'), None, 2)[2])
    original = (root / 'jobs/003.json').read_bytes()
    old_view = c.science_view(old, root)['lineage']['mechanisms']['portfolio']
    assert old_view['review_after_cycles'] == 3 and not old_view['review_reminder']
    save_controls(root, {'budget.portfolio_review_after_cycles': 2})
    assert c.science_view(old, root)['lineage']['mechanisms']['portfolio'] == old_view
    author(root, 3, [1, 2])
    portfolio_worker(root, 4, [1, 2, 3], observed=Q)
    worker_job = c.read(root / 'jobs/004.json')
    before = c.science_view(worker_job, root)['lineage']['mechanisms']['portfolio']
    assert before['review_after_cycles'] == 2 and not before['review_reminder']
    new, _ = author(root, 5, [1, 4])
    after = c.science_view(new, root)['lineage']['mechanisms']['portfolio']
    assert after['review_after_cycles'] == 2 and after['review_reminder']
    assert after['pending_assessment'] and after['overdue_count_if_assessed'] == 2
    assert search.project(root, 6)['portfolio']['invested'] == {P: 1, Q: 1}
    assert c.validate(c.workspace(root, 5))['next_action'] == 'continue_w'
    assert 'portfolio_status' not in c.read(execution.record(root, 5) / 'result-identity.json')['control_outcome']
    assert c.settings(root, old)['portfolio_policy']['review_after_cycles'] == 3
    assert (root / 'jobs/003.json').read_bytes() == original


@pytest.mark.process_e2e
def test_form_saved_four_candidate_extras_accepts_four_rejects_five_and_keeps_old_node_bound(
        tmp_path, execution_registry):
    from test_breadth_science import a, findings, h, prepare
    from test_candidate_nominations import accepted_worker
    from test_history_handoff import finish

    root = prepare(tmp_path)
    h(root)
    old, config = accepted_worker(root, tmp_path)
    old_bytes = (root / 'jobs/002.json').read_bytes()
    intent_bytes = (execution.record(root, 2) / 'native-intent.json').read_bytes()
    save_controls(root, {'budget.candidate_extra_slots': 4})
    directory = c.workspace(root, 2) / 'experiments/models'
    directory.mkdir(parents=True)
    artifacts = [f'artifacts/model-{i}.bin' for i in range(8)]
    declared = dict(version=1, metrics=[], outputs=[dict(path=path) for path in artifacts],
        candidates=[dict(artifact=path, label=f'Retained original {index}') for index, path in enumerate(artifacts)])
    script = directory / 'run.py'
    script.write_text('from pathlib import Path\n'
        'for path in ' + repr(artifacts) + ': Path(path).write_bytes(b"fixture")\n'
        'Path("artifacts/result.json").write_text(' + repr(json.dumps(declared)) + ')\n')
    reply = experiments.dispatch(config, script, mechanism='P', command_options=dict(experiment_directory=str(directory)))
    assert reply['status'] == 'known_success'
    finish(root, 2, 'W', [1], mechanism_findings=findings('P', blocked=('Q', 'R')), preserve_native_intent=True)
    observed = c.read(root / c.read(execution.record(root, 2) / 'result-identity.json')['observations']['path'])
    ids = [row['id'] for row in observed['candidates']]
    four = dict(representatives=[dict(mechanism_ref=f'h001.m{index + 1}', candidate=ids[index],
        reason='This retained candidate represents the stated scientific mechanism.') for index in range(3)],
        extras=[dict(candidate=identity, reason='A distinct retained scientific alternative.') for identity in ids[3:7]], gaps=[])
    with pytest.raises(ValueError, match='extra_slots'):
        nominations.require(root, old, dict(candidate_nominations=four))
    a(root, 3, [1, 2], focus='h001.m2')
    new = c.build_job(root, 4, 'W', [1, 2, 3])
    assert new['candidate_nomination_policy']['extra_slots'] == 4
    nominations.require(root, new, dict(candidate_nominations=four))
    five = copy.deepcopy(four)
    five['extras'].append(dict(candidate=ids[7], reason='A fifth distinct retained scientific alternative.'))
    with pytest.raises(ValueError, match='extra_slots'):
        nominations.require(root, new, dict(candidate_nominations=five))
    finish(root, 4, 'W', [1, 2, 3], mechanism_findings=findings(
        blocked=[row['mechanism_ref'] for row in new['search']['coverage_obligation']]),
        candidate_nominations=four)
    assert len(c.validate(c.workspace(root, 4))['candidate_nominations']['extras']) == 4
    assert old['candidate_nomination_policy']['extra_slots'] == 2
    assert c.settings(root, old)['candidate_nomination_policy']['extra_slots'] == 2
    assert (root / 'jobs/002.json').read_bytes() == old_bytes
    assert (execution.record(root, 2) / 'native-intent.json').read_bytes() == intent_bytes
    assert len(observed['candidates']) == 8 and len(observed['managed_calls']) == 1
