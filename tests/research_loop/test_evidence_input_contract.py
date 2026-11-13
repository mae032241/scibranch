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
    if role == 'W':
        assert 'implementation_selection:null=keep;[]=clear;IDs=inputs.' in text
    else:
        assert text.count(c.IMPLEMENTATION_REUSE) == 1
        assert 'null=keep; []=clear;' in text
        assert 'current/history IDs=next-node inputs' in text
    assert 'implementation_selection' not in c.result_schema(role)['required']




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
        public = c._science_view(job, None, parent_sources={}, policy={}, qualified_materials=[], execution_fact_view=None)
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
