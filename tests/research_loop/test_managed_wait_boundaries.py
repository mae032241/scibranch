"""Host wait classification from retained originals; no native or backend runs."""
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import budget, continuation, contracts as c, execution, managed_wait


def waiting(tmp_path, monkeypatch, *, state, seconds):
    job = dict(step=1, role='W')
    c.write(tmp_path / 'jobs/001.json', job)
    record = tmp_path / 'native-evidence/001'
    record.mkdir(parents=True)
    accepted = tmp_path / 'original/accepted.json'
    c.write(accepted, dict(call_id='original-call'))
    observed = accepted.parent / 'execution-observation.json'
    if state in {'known_success', 'known_failed'}:
        c.write(observed, dict(status=state))
    saved = {path: path.read_bytes() for path in (tmp_path / 'jobs/001.json', accepted)}
    if observed.exists():saved[observed] = observed.read_bytes()
    config = dict(execution_interface=dict(capabilities=dict(query=True)))
    queries, wakes = [], []
    monkeypatch.setattr(execution, 'record', lambda *args: record)
    monkeypatch.setattr(managed_wait, 'validate', lambda *args: dict(calls=[dict(accepted=dict(path=str(accepted)))]))
    monkeypatch.setattr(continuation, 'validate_wake_policy', lambda *args: {})
    monkeypatch.setattr(managed_wait, '_config', lambda *args: (None, config))
    monkeypatch.setattr(managed_wait.e, 'verify_observation', lambda *args, **kwargs: None)
    def status(call):
        queries.append(call)
        return dict(state=state)
    monkeypatch.setattr(managed_wait.e, 'load', lambda *args: SimpleNamespace(status_details=status))
    monkeypatch.setattr(budget, 'snapshot', lambda *args: dict(seconds_remaining=seconds))
    monkeypatch.setattr(continuation, 'wake_start', lambda root, step, proofs: wakes.append((step, proofs)))
    monkeypatch.setattr(managed_wait.time, 'sleep', lambda *args: pytest.fail('No pending poll is expected'))
    return saved, observed, queries, wakes


@pytest.mark.parametrize('state,reason', [
    ('running', 'managed_execution_wait_deadline'),
    ('known_success', 'managed_execution_terminal_wake_deadline'),
    ('known_failed', 'managed_execution_terminal_wake_deadline'),
    ('unknown', 'managed_execution_status_unknown'),
])
def test_expired_wait_distinguishes_pending_terminal_and_unknown(tmp_path, monkeypatch, state, reason):
    saved, _, queries, wakes = waiting(tmp_path, monkeypatch, state=state, seconds=0)
    with pytest.raises(managed_wait.ExecutionWaiting) as stopped:
        managed_wait.wait_and_wake(tmp_path, 1)
    assert stopped.value.reason == reason and wakes == []
    assert queries == ([] if state.startswith('known_') else ['original-call'])
    assert all(path.read_bytes() == raw for path, raw in saved.items())
    assert not (tmp_path / 'native-evidence/001/continuations').exists()


def test_verified_terminal_still_uses_original_wake_admission(tmp_path, monkeypatch):
    saved, observed, queries, _ = waiting(tmp_path, monkeypatch, state='known_success', seconds=1)
    calls = []
    def refused(root, step, proofs):
        calls.append((root, step, proofs))
        raise budget.NodeBudgetUnavailable('worker_resume_budget_unavailable')
    monkeypatch.setattr(continuation, 'wake_start', refused)
    with pytest.raises(budget.NodeBudgetUnavailable, match='worker_resume_budget_unavailable'):
        managed_wait.wait_and_wake(tmp_path, 1)
    assert calls == [(tmp_path, 1, [managed_wait.e.proof(observed)])] and queries == []
    assert all(path.read_bytes() == raw for path, raw in saved.items())
