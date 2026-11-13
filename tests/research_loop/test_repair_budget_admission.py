"""Soft repair spends available balance while preserving required closeout."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import budget, configuration_updates, contracts as c, controller, execution, native, protocol
from scidiscovery.research_loop.experiment_client import record_directory
from test_audit_engineering import cli, repair_scene
from execution_fixtures import execution_registry


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


@pytest.mark.process_e2e
@pytest.mark.parametrize('crash',[False,True])
def test_soft_ordinary_repair_reaches_one_final_delivery_and_continuation(tmp_path,monkeypatch,crash):
    prepare=controller.prepare
    def soft_scene(*args,**kwargs):
        # One extra step pays for the crash case's finite same-session attempt.
        kwargs.update(node_budget_mode='soft',max_seconds=720,max_steps=6 if crash else 5)
        kwargs['node_budget_policy']['W']=dict(max_observed_tokens=650000,max_seconds=480)
        kwargs['delivery_policy'].update(closeout_a=dict(max_observed_tokens=150000,max_seconds=120),
            inflight_tokens=200000,cleanup_seconds=60,submit_seconds=45,confirm_seconds=15)
        return prepare(*args,**kwargs)
    monkeypatch.setattr(controller,'prepare',soft_scene)
    root,_=repair_scene(tmp_path,ordinary=True,final_after_repair=True,max_tokens=1000000,crash=crash)
    original={p:p.read_bytes() for p in (root/'native-evidence').glob('*/result-identity.json')}
    first=cli(root)
    if crash:
        assert first['status']=='native_stopped',first
        stopped=execution.record(root,4)
        stopped_originals={name:(stopped/name).read_bytes() for name in (
            'native-intent.json','native-outcome.json','usage.json')}
        original_job=(root/'jobs/004.json').read_bytes()
        assert cli(root,'worker-resume','--step','4')['status']=='delivery_confirmed'
        assert (root/'jobs/004.json').read_bytes()==original_job
        assert all((stopped/name).read_bytes()==raw for name,raw in stopped_originals.items())
    else:assert first['status']=='delivery_confirmed',first
    repair=c.read(root/'jobs/004.json')
    assert repair.get('purpose') is None
    assert repair['engineering_repair']['candidate_id']=='run-1'
    attempts=execution.records(root,4)
    assert len(attempts)==(2 if crash else 1)
    if crash:
        resumed=c.read(attempts[1]/'native-intent.json')
        assert resumed['continuation_kind']=='repair'
        assert resumed['session']['thread_id']=='11111111-2222-4333-8444-555555555555'
    final=c.read(root/'jobs/005.json')
    assert final['role']=='A' and final['final_only']
    assert budget.final_a_barrier(root)['step']==5
    assert not (root/'jobs/006.json').exists()
    assert all(p.read_bytes()==data for p,data in original.items())
    work=c.workspace(root,4)
    assert repair['worker_view_version']==1
    calls=record_directory(root,4)/'calls'
    assert calls.is_relative_to(root/'host-runtime/experiment-control') and not calls.is_relative_to(work)
    originals=list(calls.iterdir())
    assert len(originals)==1
    accepted=c.read(originals[0]/'accepted.json')
    assert accepted['scope']['step']==4 and accepted['scope']['role']=='W'
    assert c.read(originals[0]/'completed.json')['status']=='known_success'
    assert not (work/'work/lineage/calls').exists()
    assert set(c.read(work/'work/experiment-config.json'))=={'worker_view_version','step','attempt'}
    assert c.settings(root)['max_tokens']==1000000 and c.settings(root)['max_seconds']==720
    assert c.settings(root)['max_steps']==(6 if crash else 5)
    assert not any(c.read(p)['kind']=='budget_amend' for p in (root/'control-ledger').glob('*.json'))
