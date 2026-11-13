"""Frozen capabilities and selection policy apply at the actual controller entry."""
import json
import re

import pytest

from scidiscovery.research_loop import budget, candidate_audit, contracts as c, controller, delivery
from test_candidate_audit import _seal_existing
from test_closeout_protocol import command, scene


@pytest.fixture(autouse=True)
def _stopped_source_seals(request, monkeypatch):
    if request.node.get_closest_marker('process_e2e') is None:
        from test_direct_input_bindings import stopped_seals
        stopped_seals(monkeypatch)


def _policy_scene(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain existing evidence and the exact scientific stop choice.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'], max_tokens=1000,
        max_seconds=60, max_steps=6, model='fixture', reasoning_effort='high', search_policy=None)
    return root


def test_h_wait_without_pending_enters_one_final(tmp_path, monkeypatch):
    from test_history_handoff import finish
    root = _policy_scene(tmp_path)
    _, _, record = finish(root, 1, 'H', [], action='wait')
    original = (record / 'science-output.json').read_bytes()
    calls = []
    monkeypatch.setattr(controller, '_run_final', lambda root, begin, *, sources=None: calls.append(begin) or dict(status='final_boundary'))
    result = controller.run(root)
    begin = budget.closeout_state(root)['begin']
    assert result['status'] == 'final_boundary' and calls == [begin]
    assert begin['mode'] == 'final' and begin['stop_reason'] == 'scientific_wait'
    assert begin['source_cutoff'] == 1 and begin['first_step'] == 2 and begin.get('resume_cursor') is None
    assert controller.run(root)['status'] == 'final_boundary' and calls == [begin, begin]
    assert (record / 'science-output.json').read_bytes() == original
    assert not (root / 'jobs/002.json').exists() and not budget.closeout_state(root)['actions']


def test_h_wait_with_pending_preserves_wait_without_final(tmp_path, monkeypatch):
    from test_history_handoff import finish
    root = _policy_scene(tmp_path)
    finish(root, 1, 'H', [], action='wait')
    monkeypatch.setattr(budget, 'execution_blocked', lambda root, **kwargs: True)
    monkeypatch.setattr(controller, '_run_final', lambda *args: pytest.fail('Pending original cannot grant FINAL'))
    assert controller.run(root)['status'] == 'wait'
    assert budget.closeout_state(root)['begin'] is None and not (root / 'jobs/002.json').exists()
