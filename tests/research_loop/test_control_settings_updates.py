"""Saved exploration controls affect real new-node gates, views and validation."""
import copy
import json

import pytest

from scidiscovery.research_loop import (candidate_nominations as nominations,
    configuration_form as form, configuration_updates as updates, contracts as c,
    controller, correction, execution, experiments, native, search)
from execution_fixtures import execution_registry
from test_configuration_form import Controls
from test_history_handoff import finish as finish_current


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


def correction_instance(tmp_path, **policies):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Compare mechanisms and retain missing observations.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-launched'], max_tokens=100_000,
        max_seconds=3600, max_steps=40, model='fixture', reasoning_effort='high',
        correction_policy=correction.DEFAULT_POLICY,**policies)
    return root


@pytest.mark.parametrize('review_after,pause_after,verdict', [(1,1,'no_progress'),(2,3,'unknown')])
def test_form_saved_correction_thresholds_bind_new_jobs_without_commanding_advice(
        tmp_path,monkeypatch,review_after,pause_after,verdict):
    root=correction_instance(tmp_path)
    old=c.build_job(root,1,'W',[])
    original={path:path.read_bytes() for path in (root/'settings.json',root/'jobs/001.json')}
    save_controls(root,{'budget.correction.review_after':review_after,
        'budget.correction.pause_after':pause_after})
    finish_current(root,1,'W',[],stage_intent='handoff')
    finish_current(root,2,'A',[1],progress=dict(verdict=verdict))
    new=c.read(root/'jobs/002.json')
    assert c.settings(root,new)['correction_policy']==dict(review_after=review_after,pause_after=pause_after)
    assert c.settings(root,old)['correction_policy']==correction.DEFAULT_POLICY
    state=correction.project(root)
    assert state['triggers']==[] and state['eligible']==[]
    assert state['review_request'] is None and state['current_pause'] is None
    assert all(path.read_bytes()==raw for path,raw in original.items())
    assert not (root/'jobs/003.json').exists()


def test_lowering_correction_thresholds_keeps_old_advice_and_job_identity(tmp_path,monkeypatch):
    root=correction_instance(tmp_path)
    finish_current(root,1,'W',[],stage_intent='handoff')
    finish_current(root,2,'A',[1],progress=dict(verdict='no_progress'))
    old=c.read(root/'jobs/002.json')
    paths=(root/'jobs/002.json',execution.record(root,2)/'science-output.json',
        execution.record(root,2)/'result-identity.json')
    originals={path:path.read_bytes() for path in paths}
    save_controls(root,{'budget.correction.review_after':1,'budget.correction.pause_after':1})
    finish_current(root,3,'W',[2],stage_intent='handoff')
    finish_current(root,4,'A',[3],progress=dict(verdict='no_progress'))
    new=c.read(root/'jobs/004.json')
    assert c.settings(root,old)['correction_policy']==correction.DEFAULT_POLICY
    assert c.settings(root,new)['correction_policy']==dict(review_after=1,pause_after=1)
    state=correction.project(root)
    assert state['review_request'] is None and state['current_pause'] is None
    assert state['triggers']==[] and state['counters']==dict(no_progress=0,unknown=0)
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    assert not (root/'jobs/005.json').exists()


def test_form_saved_portfolio_frequency_binds_only_new_jobs(tmp_path):
    root=correction_instance(tmp_path,portfolio_policy=dict(version=2,review_after_cycles=3))
    finish_current(root,1,'W',[],stage_intent='handoff')
    old=c.build_job(root,2,'A',[1])
    original=(root/'jobs/002.json').read_bytes()
    save_controls(root,{'budget.portfolio_review_after_cycles':2})
    finish_current(root,2,'A',[1])
    new=c.build_job(root,3,'W',[2])
    assert c.settings(root,old)['portfolio_policy']['review_after_cycles']==3
    assert c.settings(root,new)['portfolio_policy']['review_after_cycles']==2
    assert (root/'jobs/002.json').read_bytes()==original
    assert correction.project(root)['review_request'] is None
    assert not (root/'jobs/004.json').exists()


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
