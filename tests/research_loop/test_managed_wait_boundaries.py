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


@pytest.mark.parametrize('status,reason', [
    ('budget_exhausted', 'shared_time_exhausted'),
    ('budget_exhausted', 'shared_budget_exhausted'),
    ('budget_unavailable', 'shared_usage_unknown'),
])
def test_budget_refusal_handoff_finishes_scientific_response_without_wait(monkeypatch, status, reason):
    monkeypatch.setattr(managed_wait.e, 'Records', lambda *args: pytest.fail('Refusal must not query an original'))
    result = managed_wait.handoff({}, dict(status=status, accepted=False, reason=reason), 'run')
    assert result['action'] == 'finalize_response' and result['reason'] == reason
    assert 'normal scientific JSON' in result['instruction']
    assert 'not performed' in result['instruction'] and 'Preserve existing code' in result['instruction']
    assert 'review requests and polling' in result['instruction']
    assert 'call_id' not in result and 'poll_interval_seconds' not in result
    if status == 'budget_unavailable':
        assert 'Shared usage remains unknown' in result['instruction']
        assert 'do not report zero usage' in result['instruction']


@pytest.mark.parametrize('accepted', [None, True])
def test_budget_words_without_proven_nonacceptance_do_not_finish_original(accepted):
    result = managed_wait.handoff(None, dict(status='budget_exhausted', accepted=accepted,
        reason='shared_time_exhausted', call_id='original-call'), 'query')
    assert result['action'] == 'reconcile_or_stop'


def test_caller_error_with_proven_nonacceptance_corrects_request_without_original(monkeypatch):
    monkeypatch.setattr(managed_wait.e, 'Records', lambda *args: pytest.fail('Rejected request has no original to query'))
    result = managed_wait.handoff(None, dict(status='caller_error', accepted=False,
        error='invalid_arguments', note='Remove unsupported --change-intent.'), 'run')
    assert result['action'] == 'correct_request' and result['reason'] == 'execution_not_accepted'
    assert 'This request was not accepted' in result['instruction']
    assert 'no original call from this request to look up' in result['instruction']
    assert 'Correct the reported arguments or inputs, then submit the corrected request' in result['instruction']
    assert 'separately accepted original call' in result['instruction'] and 'never resubmit it' in result['instruction']
    assert 'call_id' not in result and 'poll_interval_seconds' not in result


@pytest.mark.parametrize('accepted', [None, True])
def test_caller_error_without_proven_nonacceptance_preserves_original(accepted, monkeypatch):
    monkeypatch.setattr(managed_wait.e, 'Records', lambda *args: pytest.fail('Unavailable response must preserve the original'))
    result = managed_wait.handoff({}, dict(status='caller_error', accepted=accepted,
        error='invalid_arguments', note='Remove unsupported --change-intent.', call_id='original-call'), 'run')
    assert result['action'] == 'reconcile_or_stop'
    assert result['reason'] == 'original_response_unavailable' and result['call_id'] == 'original-call'


@pytest.mark.parametrize('status', ['unknown', 'integrity_error', 'unresolved'])
def test_caller_error_words_do_not_authorize_corrected_execution(status, monkeypatch):
    monkeypatch.setattr(managed_wait.e, 'Records', lambda *args: pytest.fail('Missing configuration must preserve uncertainty'))
    result = managed_wait.handoff(None, dict(status=status, accepted=False, error='caller_error',
        note='This request was not accepted; remove unsupported --change-intent.', call_id='original-call'), 'run')
    assert result['action'] == 'reconcile_or_stop'
    assert 'never' in result['instruction'].lower() or 'preserve' in result['instruction'].lower()
