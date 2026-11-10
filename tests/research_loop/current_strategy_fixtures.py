"""Explicit current strategy values shared by deterministic control tests."""
from scidiscovery.research_loop import supervision


def budget_facts(fraction=.2):
    return dict(tokens=None if fraction is None else fraction * 1000,
        seconds=None if fraction is None else fraction * 100,
        limits=dict(tokens=1000, seconds=100),
        complete=dict(tokens=fraction is not None, seconds=fraction is not None))


def current_policy(**changes):
    return supervision.policy(dict(supervision.DEFAULT_POLICY, **changes))
