"""Current budget notices and pure fraction projection; no scientific authority."""
import pytest

from scidiscovery.research_loop import budget, budget_notice, contracts as c


@pytest.mark.parametrize('text',[budget_notice.WORKER_PROGRESS_TEXT,budget_notice.WORKER_CONTEXT_TEXT,
    budget_notice.WORKER_CONTEXT_UNKNOWN_TEXT])
def test_budget_notices_request_fresh_w_handoff_and_separate_direction_supervision(text):
    assert 'fresh W' in text and 'ledger/control due state' in text
    assert 'A review' not in text and 'independent A' not in text
    assert len(text.encode())<=budget_notice.MAX_NOTICE_BYTES


@pytest.mark.parametrize('tokens,seconds,expected',[(None,None,None),(20,None,.2),(None,40,.4),(20,40,.4),(120,40,1.2)])
def test_budget_fraction_is_pure_and_retains_unknowns_and_overrun(tokens,seconds,expected,monkeypatch):
    monkeypatch.setattr(c,'read',lambda *args:pytest.fail('Budget fraction must use supplied observations'))
    assert budget.supervision_fraction(initial_tokens=100,initial_seconds=100,
        observed_tokens=tokens,active_seconds=seconds)==expected
