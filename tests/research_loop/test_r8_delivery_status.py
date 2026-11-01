"""A confirmed FINAL submit still requires its reserved confirmation action."""
import pytest

from scidiscovery.research_loop import accepted_budget, budget, controller, delivery
from test_final_a_completion import external_scene


def test_r8_final_submit_confirmation_precedes_final_confirmed_status(tmp_path,monkeypatch):
    root=external_scene(tmp_path,monkeypatch)
    calls=[]
    def confirmed(selected,intent,remaining,**kwargs):
        calls.append((intent['action_kind'],intent['operation_id']))
        assert accepted_budget.read(selected)['final_confirmed'] is False
        if intent['action_kind']=='confirm':
            actions=budget.closeout_state(selected)['actions']
            assert [row['intent']['action_kind'] for row in actions]==['submit','confirm']
            assert actions[0]['outcome']['result']['status']=='confirmed'
            assert actions[1]['outcome'] is None
        return delivery._result(intent['binding'],'confirmed','Deterministic transport receipt; no external call was made.')
    monkeypatch.setattr(delivery,'invoke',confirmed)

    result=controller.closeout(root)

    assert result['status']=='delivery_confirmed',result
    assert [kind for kind,_ in calls]==['submit','confirm']
    assert len({operation for _,operation in calls})==1
    state=accepted_budget.read(root)
    assert state['final_confirmed'] is True
    actions=budget.closeout_state(root)['actions']
    assert len(actions)==2
    last=actions[-1]['outcome']['result']
    assert result['last_confirmed']==result['current_actual']==result['submission_result']==last
    intent=actions[-1]['intent']
    with pytest.raises(budget.NodeBudgetUnavailable,match='final_a_delivery_already_confirmed'):
        budget.accept_action(root,'confirm',intent['operation_id'],intent['decision'],intent['binding'],intent['max_seconds'])
    assert controller.closeout(root)['status']=='delivery_confirmed'
    assert len(budget.closeout_state(root)['actions'])==len(calls)==2


def test_r8_unknown_submit_reconciles_same_operation_without_resubmission(tmp_path,monkeypatch):
    root=external_scene(tmp_path,monkeypatch)
    calls=[]
    def reconcile(selected,intent,remaining,**kwargs):
        calls.append((intent['action_kind'],intent['operation_id']))
        assert accepted_budget.read(selected)['final_confirmed'] is False
        unknown=intent['action_kind']=='submit'
        return delivery._result(intent['binding'],'unknown' if unknown else 'confirmed',
            'Deterministic stopped-operation reconciliation fixture; no external call was made.',cleanup_confirmed=True)
    monkeypatch.setattr(delivery,'invoke',reconcile)
    first=controller.closeout(root)
    assert first['status']=='delivery_unknown'
    assert first['last_confirmed'] is None and first['current_actual']=='unknown'
    assert accepted_budget.read(root)['final_confirmed'] is False
    submit=budget.closeout_state(root)['actions'][0]
    original=root/'control-ledger'/f'{submit["outcome"]["seq"]:06d}.json'
    retained=original.read_bytes()

    result=controller.closeout(root)

    assert result['status']=='delivery_confirmed',result
    assert [kind for kind,_ in calls]==['submit','confirm']
    assert len({operation for _,operation in calls})==1
    assert accepted_budget.read(root)['final_confirmed'] is True
    assert original.read_bytes()==retained
    assert budget.closeout_state(root)['actions'][0]['outcome']['result']['status']=='unknown'
    assert controller.closeout(root)['status']=='delivery_confirmed'
    assert len(budget.closeout_state(root)['actions'])==len(calls)==2


def test_r8_unknown_cleanup_blocks_further_calls_without_resubmission(tmp_path,monkeypatch):
    root=external_scene(tmp_path,monkeypatch)
    calls=[]
    def unclean(selected,intent,remaining,**kwargs):
        calls.append((intent['action_kind'],intent['operation_id']))
        return delivery._result(intent['binding'],'unknown',
            'Deterministic unknown-cleanup receipt; no external call was made.',cleanup_confirmed=False)
    monkeypatch.setattr(delivery,'invoke',unclean)
    assert controller.closeout(root)['status']=='delivery_unknown'
    action=budget.closeout_state(root)['actions'][0]
    original=root/'control-ledger'/f'{action["outcome"]["seq"]:06d}.json'
    retained=original.read_bytes()

    assert controller.closeout(root)['status']=='delivery_unknown'

    assert [kind for kind,_ in calls]==['submit']
    assert len(budget.closeout_state(root)['actions'])==1
    assert accepted_budget.read(root)['final_confirmed'] is False
    assert original.read_bytes()==retained


@pytest.mark.parametrize('status',['failed','rejected','engineering_error','unclassified'])
def test_r8_failed_submit_never_sets_final_confirmation_or_resubmits(tmp_path,monkeypatch,status):
    root=external_scene(tmp_path,monkeypatch)
    calls=[]
    def failure(selected,intent,remaining,**kwargs):
        calls.append(intent['action_kind'])
        return delivery._result(intent['binding'],status,'Deterministic cleaned failure; no external call was made.')
    monkeypatch.setattr(delivery,'invoke',failure)
    result=controller.closeout(root)
    assert result['status']=='delivery_'+status,result
    assert result['last_confirmed'] is None
    assert accepted_budget.read(root)['final_confirmed'] is False
    assert controller.closeout(root)['status']=='delivery_'+status
    assert len(budget.closeout_state(root)['actions'])==len(calls)==1
    assert calls==['submit']
