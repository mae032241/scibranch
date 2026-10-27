"""Pure allocations; prospective interpretation never publishes a consumed plan."""
import copy

import pytest

from scidiscovery.research_loop.scientific_intent import resolve_intent
from scidiscovery.research_loop.scientific_intent import resolve_discriminating_work


def plan():
    return dict(mechanisms={'P':dict(label='P',status='pending'),'Q':dict(label='Q',status='pending')},
        authorized={'P','Q'},focus=dict(mechanism_ref='P',action='screen'),
        order=dict(remaining=[dict(mechanism_ref='P',planned_step=1),dict(mechanism_ref='Q',planned_step=1)],
            seen_w=set(),allocation=dict(mechanism_ref='P',planned_step=1)))


def assessed():
    return dict(identity=(2,'a'*64),mechanism_ref='P',allocation=dict(mechanism_ref='P',planned_step=1))


def test_explicit_screen_h_then_eligible_a_follows_original_plan_without_mutating_preview():
    state=plan();original=copy.deepcopy(state)
    first=resolve_intent(state,dict(role='H'),dict(next_action='experiment',search_plan=dict(focus=dict(label=None,action='screen'))))
    assert first['focus']==dict(mechanism_ref='P',action='screen')
    next_=resolve_intent(state,dict(role='A'),dict(next_action='continue_w',search_review=dict(next_focus=dict(mechanism_ref=None,action='screen'))),assessed=assessed())
    assert next_['focus']==dict(mechanism_ref='Q',action='screen')
    assert next_['unfinished_plan']==[dict(mechanism_ref='Q',planned_step=1)]
    assert state==original


def test_explicit_deepening_keeps_omitted_unfinished_work():
    state=plan()
    result=resolve_intent(state,dict(role='A'),dict(next_action='continue_w',route_reason='Resolve a target-condition blind spot',
        search_review=dict(next_focus=dict(mechanism_ref='P',action='improve'))),assessed=assessed())
    assert result['focus']==dict(mechanism_ref='P',action='improve')
    assert result['allocation'] is None
    assert result['unfinished_plan']==[dict(mechanism_ref='Q',planned_step=1)]


def test_same_item_repair_keeps_its_original_opportunity():
    state=plan()
    result=resolve_intent(state,dict(role='A'),dict(next_action='continue_w',route_reason='Repair the discriminating measurement',
        search_review=dict(next_focus=dict(mechanism_ref='P',action='repair'))),assessed=assessed())
    assert result['unfinished_plan']==state['order']['remaining'] and not result['seen_w']


@pytest.mark.parametrize('role,action',[('A','audit'),('A','extract'),('A','wait'),('E',None)])
def test_nonallocation_routes_never_consume_work(role,action):
    state=plan();result=resolve_intent(state,dict(role=role),dict(next_action=action),assessed=assessed())
    assert result['unfinished_plan']==state['order']['remaining'] and not result['seen_w']


def test_explicit_redirect_does_not_discard_original_plan_prefix():
    state=plan();result=resolve_intent(state,dict(role='H'),dict(next_action='experiment',route_reason='Prioritize an independent falsifier',
        search_plan=dict(focus=dict(label='Q',action='screen'))))
    assert result['focus']['mechanism_ref']=='Q'
    assert result['unfinished_plan']==state['order']['remaining']


def test_ambiguous_label_and_empty_plan_have_scientific_diagnostics():
    state=plan();state['mechanisms']['P']['label']=state['mechanisms']['Q']['label']='same'
    ambiguous=resolve_intent(state,dict(role='H'),dict(next_action='experiment',route_reason='Scientific choice',
        search_plan=dict(focus=dict(label='same',action='screen'))))
    assert ambiguous['diagnostics'][0]['code']=='science_focus_ambiguous'
    state['order']['remaining']=[]
    empty=resolve_intent(state,dict(role='H'),dict(next_action='experiment',search_plan=dict(focus=dict(label=None,action='screen'))))
    assert empty['diagnostics'][0]['code']=='science_plan_empty'


def test_internal_state_damage_is_host_error_not_scientific_clarification():
    state=plan();del state['order']
    with pytest.raises(KeyError):resolve_intent(state,dict(role='H'),dict(next_action='experiment'))


def test_optional_work_details_cannot_replace_the_actual_author_identity():
    state = plan()
    state['order']['discriminating_work'] = [dict(work_ref='A003-D001', status='deferred', updates=[], executions=[])]
    result = resolve_discriminating_work(state, dict(role='A', step=5), dict(next_action='continue_w',
        portfolio_review=dict(work_updates=[dict(work_ref='A003-D001', action='defer',
            reason='Revisit after the missing input arrives.', source_a='optional scientific detail')])) )
    assert result[0]['updates'][0]['source_a'] == 5
    assert result[0]['status'] == 'deferred'
    assert not state['order']['discriminating_work'][0]['updates']


@pytest.mark.parametrize('role,action,choice_key,focus_key',[('H','experiment','search_plan','focus'),('A','continue_w','search_review','next_focus')])
@pytest.mark.parametrize('choice',[None,{}])
def test_missing_explicit_task_action_is_diagnostic_without_implicit_screen(role,action,choice_key,focus_key,choice):
    state=plan();before=copy.deepcopy(state)
    value=None if choice is None else {focus_key:None}
    result=resolve_intent(state,dict(role=role),dict(next_action=action,**{choice_key:value}))
    assert result['focus'] is None and result['allocation'] is None
    assert result['diagnostics']==[dict(code='science_task_action_required',subject='W scientific task')]
    assert result['unfinished_plan']==before['order']['remaining'] and state==before
