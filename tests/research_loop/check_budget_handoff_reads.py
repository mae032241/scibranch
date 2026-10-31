"""Explicit ledger-read bounds within one budget operation, never across calls."""
from contextlib import contextmanager

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller


def _ledger(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain fresh budget ceilings and the original active clock.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'], max_tokens=1000,
        max_seconds=60, max_steps=4, model='fixture', reasoning_effort='high',
        search_policy=None)
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'paused', 'fixture_start')
    return root


def _count_reads(monkeypatch):
    reads = []
    original = budget._read_events
    def counted(*args, **kwargs):
        reads.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(budget, '_read_events', counted)
    return reads


@pytest.mark.parametrize('phase', ['paused', 'waiting'])
def test_status_snapshot_uses_one_ledger_observation(tmp_path, monkeypatch, phase):
    root = _ledger(tmp_path)
    budget.transition(root, phase, 'fixture_phase')
    reads = _count_reads(monkeypatch)
    assert budget.status_snapshot(root)['clock_phase'] == phase
    assert len(reads) == 1


def test_transition_reuses_its_locked_events_and_next_call_sees_new_phase(tmp_path, monkeypatch):
    root = _ledger(tmp_path)
    reads = _count_reads(monkeypatch)
    budget.transition(root, 'waiting', 'first_call')
    assert len(reads) == 1
    budget.transition(root, 'paused', 'next_call')
    assert len(reads) == 2
    assert budget.snapshot(root)['clock_phase'] == 'paused'
    assert len(reads) == 3


def test_closeout_reuses_its_locked_events(tmp_path, monkeypatch):
    root = _ledger(tmp_path)
    reads = _count_reads(monkeypatch)
    first = budget.begin_closeout(root, 0, 1, 'fixture_boundary', export_only=True)
    assert len(reads) == 1
    assert budget.begin_closeout(root, 0, 1, 'unchanged_boundary', export_only=True) == first
    assert len(reads) == 2


def test_amendment_reads_once_inside_its_writer_and_next_snapshot_is_fresh(tmp_path, monkeypatch):
    root = _ledger(tmp_path)
    original_writer = budget._ledger_writer
    original_read = budget._read_events
    locked_reads = []
    depth = [0]
    @contextmanager
    def writer(*args, **kwargs):
        with original_writer(*args, **kwargs):
            depth[0] += 1
            try:
                yield
            finally:
                depth[0] -= 1
    def counted(*args, **kwargs):
        if depth[0]:
            locked_reads.append(True)
        return original_read(*args, **kwargs)
    monkeypatch.setattr(budget, '_ledger_writer', writer)
    monkeypatch.setattr(budget, '_read_events', counted)
    before = budget.snapshot(root)
    budget.amend(root, max_tokens=1200, max_seconds=90, authorization_note='Explicit fixture amendment.')
    assert len(locked_reads) == 1
    after = budget.snapshot(root)
    assert before['max_tokens'] == 1000 and after['max_tokens'] == 1200
    assert before['max_seconds'] == 60 and after['max_seconds'] == 90
    assert after['ledger_events'] == before['ledger_events'] + 1
