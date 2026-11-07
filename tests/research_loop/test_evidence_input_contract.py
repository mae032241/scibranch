"""Retained identity, task inputs and call access are distinct capabilities."""
import copy
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import (
    contracts as c, experiment_client, experiments, guidance, history,
    originals, shared_input_views,
)
from scidiscovery.research_loop.protocol import stage_scope
from test_direct_input_bindings import direct_instance, stopped_seals
from test_history_handoff import finish


def readonly_binding(arguments, source, target):
    return any(arguments[index:index + 3] == ['--ro-bind', str(source), str(target)]
        for index in range(len(arguments) - 2))


@pytest.mark.parametrize('role', ['H', 'W', 'A', 'E'])
def test_selection_contract_exposes_exact_history_ids_and_deferred_input_binding(role):
    instructions = c.response_instructions(dict(role=role))
    text = instructions['stage_response' if role == 'W' else 'implementation_reuse']
    assert text.count(c.IMPLEMENTATION_REUSE) == 1
    assert 'null=keep; []=clear;' in text
    assert 'current/history IDs=next-node inputs' in text
    assert 'implementation_selection' not in c.result_schema(role)['required']


def test_e_selects_unmounted_history_original_for_next_a_without_auto_mounting_siblings(
        tmp_path, monkeypatch):
    root, _ = direct_instance(tmp_path)
    stopped_seals(monkeypatch)
    finish(root, 1, 'H', [], action='experiment')
    _, old_work, _ = finish(root, 2, 'W', [1], files={
        'kept.json': '{"observation":"original"}',
        'unused.json': '{"observation":"unrequested sibling"}',
    })
    finish(root, 3, 'A', [1, 2], action='revise_h')
    finish(root, 4, 'H', [1, 2, 3], action='experiment')
    worker, _, _ = finish(root, 5, 'W', [4], files={'report.txt': 'A new bounded report'})
    retained_path = 'inputs/parents/002/artifacts/kept.json'
    assert retained_path in {row['path'] for row in worker['direct_materials']}
    assessment, _, _ = finish(root, 6, 'A', [4, 5], action='extract')
    assert {row['path'] for row in assessment['direct_materials']} == {
        'inputs/parents/005/artifacts/report.txt'}

    parents = [4, 5, 6]
    evidence = c.build_job(root, 7, 'E', parents, instruction='Exact scientific question',
        evidence_return=dict(role='A', step=6))
    work = c.workspace(root, 7)
    retained = history.resolve_implementation(root, 'W002-I001', evidence['history']['prefix'])
    original = originals.material_path(root, retained)
    before = original.read_bytes()
    assert original == old_work / 'artifacts/kept.json'
    assert retained not in evidence['direct_materials']
    assert 'W002-I001' not in {row['id'] for row in evidence['implementation_options']}
    arguments = shared_input_views.mount_arguments(root, evidence, work, include_skills=False)
    assert not readonly_binding(arguments, original, work / retained_path)
    config = dict(record_dir=str(experiment_client.record_directory(root, 7)),
        scope=stage_scope(root, evidence))
    with pytest.raises(experiments.IntegrityError, match='outside this task bound inputs'):
        experiment_client.input_reference(config, work / retained_path)
    with pytest.raises(experiments.IntegrityError, match='outside this task bound inputs'):
        experiment_client.input_reference(config, original)

    current, _, _ = finish(root, 7, 'E', parents, selection=['W002-I001'])
    assert retained not in current['direct_materials']  # Selection changes a successor, not this turn.
    successor = c.build_job(root, 8, 'A', [4, 5, 6, 7])
    selected = [row for row in successor['direct_materials'] if row['path'] == retained_path]
    assert selected == [retained]
    assert 'inputs/parents/002/artifacts/unused.json' not in {
        row['path'] for row in successor['direct_materials']}
    arguments = shared_input_views.mount_arguments(root, successor, c.workspace(root, 8),
        include_skills=False)
    assert readonly_binding(arguments, original, c.workspace(root, 8) / retained_path)
    assert original.read_bytes() == before
    reading = c.read(c.workspace(root, 7) / 'science-task.json')['reading_order']
    for fact in ('calls addressable by the current experiment client',
            'assigned originals or select exact frozen-history I IDs for next-node inputs after return',
            'Retained identity alone implies neither readable material nor queryable calls',
            'Prose/requested_evidence/evidence_requests do not bind inputs',
            'do not search private control records'):
        assert fact in reading


@pytest.mark.parametrize('status', ['known_success', 'known_failed'])
def test_repair_projects_existing_terminal_status_without_reopening_source(
        tmp_path, monkeypatch, status):
    root, _ = direct_instance(tmp_path)
    job = c.build_job(root, 1, 'W', [])
    job['search'] = dict(focus=dict(mechanism_ref='h001.m1', action='repair'),
        repair_source=dict(step=2, call=dict(call_id='historical-call', status=status,
            accepted=dict(snapshot='PRIVATE-ACCEPTED'),
            terminal=dict(snapshot='PRIVATE-TERMINAL'), hidden='PRIVATE-CONTROL')),
        repair_authorization=dict(step=4, seal_ref=dict(path='PRIVATE-SEAL')))
    saved = copy.deepcopy(job)

    def forbidden(*args, **kwargs):
        pytest.fail('Scientific projection reopened an owner-provided observation')

    with monkeypatch.context() as projection:
        projection.setattr(c, 'read', forbidden)
        projection.setattr(c, 'regular', forbidden)
        for method in ('open', 'read_text', 'read_bytes', 'stat', 'lstat'):
            projection.setattr(Path, method, forbidden)
        public = c._science_view(job, None, parent_sources={}, policy={}, qualified_materials=[])
    observation = public['repair_observation']
    assert set(observation) == {
        'mechanism_ref', 'source_step', 'call_id', 'status', 'authorization_step', 'scope'}
    assert observation['status'] == status
    assert observation['call_id'] == 'historical-call'
    assert observation['source_step'] == 2 and observation['authorization_step'] == 4
    assert 'Scientific repair under current H' in observation['scope']
    assert 'terminal history, not lifecycle restoration' in observation['scope']
    assert 'PRIVATE-' not in json.dumps(public)
    assert job == saved
    assert 'exact scientific observation' in guidance.WORK_UNITS['repair']
