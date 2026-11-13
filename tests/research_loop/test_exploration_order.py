"""Retired priority option rejection and current unfinished-plan index originals."""
import json

import pytest

from scidiscovery.research_loop import contracts as c, controller, history, protocol, search
from test_history_handoff import finish


def test_retired_author_priority_option_rejects_before_creating_instance(tmp_path):
    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Keep explicit scientific choices and unfinished comparisons.')
    original = objective.read_bytes()
    root = tmp_path / 'instance'
    with pytest.raises(ValueError, match='Author priority-table strategy is retired'):
        controller.prepare(root, objective, public, ['never-launched'], max_tokens=100_000,
            max_seconds=3600, max_steps=16, model='fixture', reasoning_effort='high',
            exploration_order_version=1)
    assert not root.exists()
    assert objective.read_bytes() == original
