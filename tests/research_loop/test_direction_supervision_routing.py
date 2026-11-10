"""Direction supervision changes prospective routes without replaying accepted work."""
import copy
from functools import partial

import pytest

from scidiscovery.research_loop import accepted_budget, accepted_search, budget, contracts as c, controller, supervision
from scidiscovery.research_loop.routing import Route
from current_strategy_fixtures import budget_facts, current_policy


select_route=partial(controller._supervision_route,source_job=dict(role='W'))


def summary(rounds=4):
    value=accepted_search.empty()
    value['last_h']=1
    value['last_w']=2
    for number in range(rounds):
        value['supervision']=supervision.add_round(value['supervision'],dict(round_id=f'batch-{number}',
            source_step=2,pointer=f'/exploration_rounds/{number}',mechanism='h001.m1',
            question='Does the retained direction address the target condition?',
            finding='An explicit synthetic batch finding; scientific suitability remains unqualified.'))
        value['supervision']=supervision.add_investment(value['supervision'],dict(
            original_call_id=f'original-{number}',mechanism_ref='w002.question',completed_receipt=True))
    return value


def facts(value=None,fraction=.2):
    return dict(summary=summary() if value is None else value,budget_fraction=fraction,
        policy=supervision.policy(),budget_facts=budget_facts(fraction))


def route(role='W',evidence_return=None):
    return Route(role=role,parents=[1,2],instruction='Retain the exact unfinished W question.',
        evidence_return=evidence_return,current_h=1,current_w=2)


def test_current_original_receipts_insert_one_diagnostic_a_and_preserve_inputs():
    result=dict(role='W',stage_intent='handoff',route_reason='Should the retained direction continue?')
    selected,binding=select_route(2,result,route(),facts=facts())
    assert selected.role=='A' and selected.parents==(1,2) and selected.evidence_return is None
    assert 'exact retained W question' in selected.instruction
    assert 'W decides' in selected.instruction and 'Original objective and permissions persist' in selected.instruction
    assert binding['automatic'] is True and binding['final'] is False
    assert binding['cutoff']==dict(total_rounds=4,source_step=2,round_id='batch-3')
    assert binding['due_reasons']==['changed_authored_question_or_original_receipt']


@pytest.mark.parametrize('next_role',['H','A'])
def test_due_supervision_preserves_consultation_and_only_merges_a(next_role):
    selected,binding=select_route(2,dict(role='W',route_reason='Latest W decision question'),
        route(next_role,dict(role='W',step=2)),facts=facts())
    assert selected==route(next_role,dict(role='W',step=2))
    assert selected.evidence_return==dict(role='W',step=2)
    if next_role=='A':assert binding['automatic'] is True
    else:assert binding is None


def test_already_scheduled_ordinary_a_merges_scope_without_an_automatic_insertion():
    original=route('A')
    selected,binding=select_route(2,dict(role='W'),original,facts=facts())
    assert selected==original and binding['automatic'] is False
    assert binding['cutoff']['total_rounds']==4


def test_ordinary_a_before_any_due_threshold_gets_no_supervision_contract():
    original=route('A')
    assert select_route(2,dict(role='W'),original,facts=facts(summary(0),.05))==(original,None)


@pytest.mark.parametrize('changed', [dict(formal_review=True),dict(purpose='formal_review'),
    dict(purpose='closeout'),dict(final_only=True),dict(final_report={'source_cutoff':2}),
    dict(engineering_repair={'source':2})])
def test_formal_final_and_engineering_sources_are_not_replaced(changed):
    original=route()
    assert select_route(2,dict(role='A'),original,facts=facts(),source_job=dict(role='A',**changed))==(original,None)


@pytest.mark.parametrize('selected', [
    Route(role='E',parents=[1,2],instruction='Answer the existing bounded evidence request',evidence_return=dict(role='A',step=2),current_h=1,current_w=2),
    Route(role='H',parents=[1,2],instruction='An existing scientific correction',current_h=1,current_w=2,correction_request={'reason':'correction'}),
    Route(role='A',parents=[1,2],instruction='An exact author correction',current_h=1,current_w=2,author_correction={'reason':'author_correction'}),
    Route(role='A',parents=[1,2],instruction='An exact formal review',evidence_return=dict(kind='formal_review'),current_h=1,current_w=2),
])
def test_bounded_evidence_formal_and_correction_routes_keep_their_original_scope(selected):
    assert select_route(2,dict(role='A'),selected,facts=facts())==(selected,None)


def test_requesting_formal_review_is_not_intercepted():
    original=route('A')
    assert select_route(2,dict(role='A',next_action='formal_review'),original,facts=facts())==(original,None)


@pytest.mark.parametrize('unknown,expected',[(False,.4),(True,.4)])
def test_fraction_producer_uses_one_head_and_initial_denominators(tmp_path,monkeypatch,unknown,expected):
    head=accepted_budget.AcceptedState(tmp_path,dict(search=summary(),seq=0),
        base=dict(max_tokens=10000,max_seconds=1000))
    reads=[]
    monkeypatch.setattr(accepted_budget,'read',lambda root:reads.append(root) or head)
    def usage(root,*,head):
        assert head.base['max_tokens']==10000
        return dict(accounted_tokens=40,observed_tokens=40,blocking_unknown_count=int(unknown))
    def sample(root,base,events):
        assert base is head.base and events.state is head
        return dict(consumed_seconds=35.)
    monkeypatch.setattr(accepted_budget,'usage',usage)
    monkeypatch.setattr(budget,'_snapshot',sample)
    settings=dict(search_policy={},supervision_policy=supervision.policy(),
        supervision_budget_basis=dict(max_tokens=100,max_seconds=100))
    produced=controller._supervision_facts(tmp_path,settings)
    assert produced['budget_fraction']==expected and reads==[tmp_path]


def test_routing_consumer_never_reloads_originals(tmp_path,monkeypatch):
    loaded=facts()
    monkeypatch.setattr(c,'read',lambda *args,**kwargs:pytest.fail('Routing consumes supplied accepted facts'))
    selected,binding=select_route(2,dict(role='W'),route(),facts=loaded)
    assert selected.role=='A' and binding['automatic'] is True


@pytest.mark.parametrize('fraction,expected',[(None,False),(.099,False),(.10,True),(.65,True)])
def test_explicit_operational_review_uses_known_budget_without_fabricating_input(fraction,expected):
    value=summary(0)
    value['supervision']=supervision.empty(current_policy(require_fresh_input=False))
    supplied=facts(value,fraction);supplied['policy']=current_policy(require_fresh_input=False)
    selected,binding=select_route(2,dict(role='W'),route(),facts=supplied)
    assert (selected.role=='A') is expected
    if expected:
        assert binding['cutoff']['total_rounds']==0
        assert binding['automatic'] is True and binding['budget_fraction']==fraction
    else:
        assert binding is None


def test_cooldown_and_maximum_keep_due_state_without_inserting_more_a():
    value=summary()
    value['supervision']['policy']=current_policy(max_automatic_reviews=2)
    frozen=supervision.request(value,budget_fraction=.3,automatic=True,budget_facts=budget_facts(.3))
    value['supervision']=supervision.cover(value['supervision'],frozen,source_step=3,
        completion_fraction=.3,budget_facts=budget_facts(.3))
    value['supervision']=supervision.add_investment(value['supervision'],dict(
        original_call_id='new-original',mechanism_ref='w004.question',completed_receipt=True))
    supplied=facts(value,.35);supplied['policy']=current_policy(max_automatic_reviews=2)
    before=copy.deepcopy(value)
    selected,binding=select_route(4,dict(role='W'),route(),facts=supplied)
    assert selected.role=='W' and binding is None and value==before
    value['supervision']['automatic_reviews']=2
    supplied=facts(value,.9);supplied['policy']=current_policy(max_automatic_reviews=2)
    selected,binding=select_route(4,dict(role='W'),route(),facts=supplied)
    assert selected.role=='W' and binding is None
    selected,binding=select_route(4,dict(role='W'),route('A'),facts=supplied)
    assert selected.role=='A' and binding['automatic'] is False


def test_terminal_and_missing_safe_w_take_priority_over_automatic_supervision():
    terminal=Route(role=None,current_h=1,current_w=2)
    assert select_route(2,dict(role='A',next_action='finish'),terminal,facts=facts())==(terminal,None)
    value=summary(0);value['last_w']=None
    assert select_route(1,dict(role='H'),route(),facts=facts(value,.8))==(route(),None)


def test_saved_route_retains_exact_scope_on_cold_resume(tmp_path,monkeypatch):
    monkeypatch.setattr(c,'settings',lambda *args:dict(goal_id='fixture-instance'))
    loaded=[]
    monkeypatch.setattr(controller.handoff_state,'read_sealed',lambda root,step:loaded.append(step) or dict(role='W'))
    frozen=supervision.request(summary(),budget_fraction=.4,automatic=True,budget_facts=budget_facts(.4))
    controller._save_route(tmp_path,2,Route(role='A',parents=[1,2],instruction='Exact W question',current_h=1,current_w=2),
        resumed=None,supervision_request=frozen)
    path=tmp_path/'host-history/controller-route.json'
    original=path.read_bytes()
    saved=controller._saved_route(tmp_path,first_step=1,resumed=None,head=None)
    assert saved['supervision_request']==frozen and loaded==[2]
    assert path.read_bytes()==original


def test_retained_job_rejects_a_changed_supervision_cutoff(tmp_path,monkeypatch):
    frozen=supervision.request(summary(),budget_fraction=.4,automatic=True,budget_facts=budget_facts(.4))
    job=dict(step=3,role='A',goal_text='Goal',public_root='/public',direct_refs=[],
        instruction='Exact question',evidence_return=None,supervision_request=frozen)
    monkeypatch.setattr(c,'read_job',lambda *args,**kwargs:job)
    settings=dict(objective='Goal',public_root='/public')
    assert controller._check_job(tmp_path,settings,3,'A',[],'Exact question',None,
        supervision_request=frozen)==job
    changed=copy.deepcopy(frozen);changed['cutoff']['total_rounds']=3
    with pytest.raises(ValueError,match='Retained job differs'):
        controller._check_job(tmp_path,settings,3,'A',[],'Exact question',None,
            supervision_request=changed)


@pytest.mark.parametrize('role',['H','W','E'])
@pytest.mark.parametrize('action',['finish','deliver'])
def test_only_a_can_terminate_research(tmp_path,role,action):
    with pytest.raises(ValueError,match='Only an independent A'):
        controller._terminal_route(tmp_path,{},2,dict(next_action=action),role)
