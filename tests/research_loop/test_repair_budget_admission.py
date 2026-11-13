"""Soft repair spends available balance while preserving required closeout."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import budget, configuration_updates, contracts as c, controller, native, protocol


def balances(monkeypatch,*,tokens=952486,seconds=717.075,steps=4,unknown=False,blocked=False,worker=True):
    # Ordinary work prepays no FINAL/delivery reserve (budget.delivery_reserve):
    # repair needs only a positive shared W balance and one remaining step.
    settings=dict(handoff_protocol=protocol.CURRENT,research_strategy_version=3,advisory_scope_version=1,
        node_budget_mode='soft',final_a_completion_policy=dict(version=1),max_steps=20,
        node_budget_policy=dict(W=dict(max_observed_tokens=650000,max_seconds=480)) if worker else {},
        delivery_policy=dict(closeout_a=dict(max_observed_tokens=150000,max_seconds=120),inflight_tokens=200000,
            cleanup_seconds=60,submit_seconds=45,confirm_seconds=15,adapter={}))
    monkeypatch.setattr(configuration_updates,'current',lambda root:settings)
    monkeypatch.setattr(c,'settings',lambda root,job=None:settings)
    monkeypatch.setattr(budget,'snapshot',lambda root:dict(max_tokens=2000000,seconds_remaining=seconds,max_steps=20))
    monkeypatch.setattr(native,'usage',lambda root:dict(observed_tokens=2000000-tokens,unknown_steps=[3] if unknown else []))
    monkeypatch.setattr(budget,'effective_max_steps',lambda root:settings['max_steps'])
    monkeypatch.setattr(budget,'used_steps',lambda root:settings['max_steps']-steps)
    monkeypatch.setattr(budget,'execution_blocked',lambda root:blocked)


def test_actual_remaining_balance_allows_soft_repair_not_full_target_reservation(monkeypatch):
    balances(monkeypatch)
    assert controller._repair_funds(Path('/unused'))


@pytest.mark.parametrize('tokens,seconds,steps,expected',[
    (1,0.001,1,True),
    (0,240.001,4,False),
    (350001,0,4,False),
    (350001,240.001,0,False),
])
def test_soft_repair_requires_positive_worker_balance_and_one_step(monkeypatch,tokens,seconds,steps,expected):
    balances(monkeypatch,tokens=tokens,seconds=seconds,steps=steps)
    assert controller._repair_funds(Path('/unused')) is expected


def test_repair_requires_explicit_worker_node_policy(monkeypatch):
    balances(monkeypatch,worker=False)
    assert controller._repair_funds(Path('/unused')) is False


@pytest.mark.parametrize('fault',['unknown','blocked'])
def test_uncertain_usage_or_external_action_never_becomes_repair_funds(monkeypatch,fault):
    balances(monkeypatch,tokens=2000000,seconds=1200,**{fault:True})
    assert not controller._repair_funds(Path('/unused'))
