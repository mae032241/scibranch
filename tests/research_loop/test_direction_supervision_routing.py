"""Direction supervision changes prospective routes without replaying accepted work."""
import copy
from functools import partial

import pytest

from scidiscovery.research_loop import accepted_budget, accepted_search, budget, contracts as c, controller, supervision


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
    return value


def facts(value=None,fraction=.2):
    return dict(summary=summary() if value is None else value,budget_fraction=fraction,
        policy=supervision.policy())


def route(role='W',evidence_return=None):
    return (role,[1,2],'Retain the exact unfinished W question.',evidence_return,1,2,None,None)


def test_four_explicit_rounds_insert_one_ordinary_a_and_preserve_question_and_inputs():
    result=dict(role='W',stage_intent='handoff',route_reason='Should the retained direction continue?')
    selected,binding=select_route(2,result,route(),facts=facts())
    assert selected[0]=='A' and selected[1]==[1,2] and selected[3] is None
    assert result['route_reason'] in selected[2] and 'continue_existing' in selected[2]
    assert binding['automatic'] is True and binding['final'] is False
    assert binding['cutoff']==dict(total_rounds=4,source_step=2,round_id='batch-3')
    assert binding['due_reasons']==['consecutive_rounds']


@pytest.mark.parametrize('next_role',['H','A'])
def test_due_supervision_replaces_consultation_with_global_ordinary_a(next_role):
    selected,binding=select_route(2,dict(role='W',route_reason='Latest W decision question'),
        route(next_role,dict(role='W',step=2)),facts=facts())
    assert selected[0]=='A' and selected[3] is None
    assert binding['automatic'] is True and selected[6:]==(None,None)
    assert 'Latest W decision question' in selected[2]


def test_already_scheduled_ordinary_a_merges_scope_without_an_automatic_insertion():
    original=route('A')
    selected,binding=select_route(2,dict(role='W'),original,facts=facts())
    assert selected==original and binding['automatic'] is False
    assert binding['cutoff']['total_rounds']==4


def test_ordinary_a_before_any_due_threshold_gets_no_supervision_contract():
    original=route('A')
    assert select_route(2,dict(role='W'),original,facts=facts(summary(3),.2))==(original,None)


@pytest.mark.parametrize('changed', [dict(formal_review=True),dict(purpose='formal_review'),
    dict(purpose='closeout'),dict(final_only=True),dict(final_report={'source_cutoff':2}),
    dict(engineering_repair={'source':2})])
def test_formal_final_and_engineering_sources_are_not_replaced(changed):
    original=route()
    assert select_route(2,dict(role='A'),original,facts=facts(),source_job=dict(role='A',**changed))==(original,None)


@pytest.mark.parametrize('selected', [
    ('E',[1,2],'Answer the existing bounded evidence request',dict(role='A',step=2),1,2,None,None),
    ('H',[1,2],'An existing scientific correction',None,1,2,{'reason':'correction'},None),
    ('A',[1,2],'An exact author correction',None,1,2,None,{'reason':'author_correction'}),
    ('A',[1,2],'An exact formal review',dict(kind='formal_review'),1,2,None,None),
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
    assert selected[0]=='A' and binding['automatic'] is True


@pytest.mark.parametrize('fraction,expected',[(None,False),(.29,False),(.30,True),(.65,True)])
def test_budget_fallback_uses_frozen_fraction_without_fabricating_rounds(fraction,expected):
    value=summary(0)
    selected,binding=select_route(2,dict(role='W'),route(),facts=facts(value,fraction))
    assert (selected[0]=='A') is expected
    if expected:
        assert binding['cutoff']['total_rounds']==0
        assert binding['automatic'] is True and binding['budget_fraction']==fraction
    else:
        assert binding is None


def test_cooldown_and_maximum_keep_due_state_without_inserting_more_a():
    value=summary()
    frozen=supervision.request(value,budget_fraction=.3,automatic=True)
    value['supervision']=supervision.cover(value['supervision'],frozen,source_step=3)
    for number in range(4,8):
        value['supervision']=supervision.add_round(value['supervision'],dict(round_id=f'batch-{number}',
            source_step=4,pointer=f'/exploration_rounds/{number-4}',mechanism='h001.m1',question='A new batch?',finding='New fixture evidence.'))
    before=copy.deepcopy(value)
    selected,binding=select_route(4,dict(role='W'),route(),facts=facts(value,.35))
    assert selected[0]=='W' and binding is None and value==before
    assert supervision.due(value,.35)['due'] is True
    value['supervision']['automatic_reviews']=2
    selected,binding=select_route(4,dict(role='W'),route(),facts=facts(value,.9))
    assert selected[0]=='W' and binding is None
    # Existing scientific A still covers a meaningful scope without spending an
    # extra automatic slot, even when control cannot insert another review.
    selected,binding=select_route(4,dict(role='W'),route('A'),facts=facts(value,.9))
    assert selected[0]=='A' and binding['automatic'] is False


def test_terminal_and_missing_safe_w_take_priority_over_automatic_supervision():
    terminal=(None,[],'',None,1,2,None,None)
    assert select_route(2,dict(role='A',next_action='finish'),terminal,facts=facts())==(terminal,None)
    value=summary(0);value['last_w']=None
    assert select_route(1,dict(role='H'),route(),facts=facts(value,.8))==(route(),None)


def test_saved_route_retains_exact_scope_on_cold_resume(tmp_path,monkeypatch):
    monkeypatch.setattr(c,'settings',lambda *args:dict(goal_id='fixture-instance'))
    loaded=[]
    monkeypatch.setattr(controller.handoff_state,'read_sealed',lambda root,step:loaded.append(step) or dict(role='W'))
    frozen=supervision.request(summary(),budget_fraction=.4,automatic=True)
    controller._save_route(tmp_path,2,('A',[1,2],'Exact W question',None,None,None),1,2,
        resumed=None,supervision_request=frozen)
    path=tmp_path/'host-history/controller-route.json'
    original=path.read_bytes()
    saved=controller._saved_route(tmp_path,first_step=1,resumed=None)
    assert saved['supervision_request']==frozen and loaded==[2]
    assert path.read_bytes()==original


def test_retained_job_rejects_a_changed_supervision_cutoff(tmp_path,monkeypatch):
    frozen=supervision.request(summary(),budget_fraction=.4,automatic=True)
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
