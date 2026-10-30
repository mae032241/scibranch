"""Pure allocations; prospective interpretation never publishes a consumed plan."""
import copy

import pytest

from scidiscovery.research_loop import breadth, protocol, search
from scidiscovery.research_loop.scientific_intent import resolve_intent
from scidiscovery.research_loop.scientific_intent import resolve_discriminating_work


def plan():
    return dict(mechanisms={'P':dict(label='P',status='pending'),'Q':dict(label='Q',status='pending')},
        authorized={'P','Q'},focus=dict(mechanism_ref='P',action='screen'),
        order=dict(remaining=[dict(mechanism_ref='P',planned_step=1),dict(mechanism_ref='Q',planned_step=1)],
            seen_w=set(),allocation=dict(mechanism_ref='P',planned_step=1)))


def assessed():
    return dict(identity=(2,'a'*64),mechanism_ref='P',allocation=dict(mechanism_ref='P',planned_step=1))


def h_plan(index=None, action='screen'):
    return dict(commitments=[dict(label=ref,minimal_test='A discriminating measurement',existing_ref=ref)
        for ref in ('P','Q')],focus=dict(index=index,action=action))


def w_combination(ref='h001.m1'):
    return dict(version=1,initial_calls=1,prior_unknown=False,grant=None,
        mechanisms=[dict(mechanism_ref=ref,label='Scientific display title',source_h=1,
            source_h_ref=dict(path='original-h-seal'),minimal_test='A discriminating measurement',status='pending')],
        baseline=[dict(mechanism_ref=ref,accepted_count=0)])


def test_explicit_screen_h_then_eligible_a_follows_original_plan_without_mutating_preview():
    state=plan();original=copy.deepcopy(state)
    first=resolve_intent(state,dict(role='H'),dict(next_action='experiment',search_plan=h_plan()))
    assert first['focus']==dict(mechanism_ref='P',action='screen')
    assert first['unfinished_plan']==state['order']['remaining'] and not first['diagnostics']
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
    state=plan();result=resolve_intent(state,dict(role='H',step=4),dict(next_action='experiment',route_reason='Prioritize an independent falsifier',
        search_plan=h_plan(2)))
    assert result['focus']['mechanism_ref']=='Q'
    assert result['unfinished_plan']==state['order']['remaining']


def test_empty_plan_has_scientific_diagnostic():
    state=plan()
    state['order']['remaining']=[]
    empty=resolve_intent(state,dict(role='H'),dict(next_action='experiment',search_plan=h_plan()))
    assert empty['diagnostics'][0]['code']=='science_plan_empty'


@pytest.mark.parametrize('index',[0,-1,3,True,False,'1',1.0,[],{}])
def test_h_invalid_index_is_repairable_without_allocating(index):
    state=plan();before=copy.deepcopy(state)
    payload=dict(next_action='experiment',route_reason='A scientific choice',search_plan=h_plan(index))
    pure=resolve_intent(state,dict(role='H',step=4),payload)
    parsed=search.interpret(None,dict(role='H',step=4),payload,state=state)
    for result in (pure,parsed):
        assert result['diagnostics']==[dict(code='science_choice_unresolved',subject='focus')]
    assert pure['focus'] is None and pure['allocation'] is None
    assert state==before
    # A rejected submission cannot carry the previous accepted focus into W.
    receipt=dict(binding_protocol=protocol.CURRENT,search_status='unresolved',selection_status='applied',decision_status='applied',
        diagnostics=parsed['diagnostics'],resolved_focus=None,resolved_allocation=None)
    search.apply_source(None,dict(role='H',step=4,handoff_protocol=protocol.CURRENT),payload,dict(control_outcome=receipt),state)
    assert state['focus'] is None and state['order']['allocation'] is None
    assert state['authorized']==before['authorized'] and state['order']['remaining']==before['order']['remaining']


@pytest.mark.parametrize('focus',[dict(label='Q',action='screen'),dict(label=None,action='screen'),
    dict(index=1,label='P',action='screen'),dict(action='screen')])
def test_h_old_label_or_missing_index_is_repairable(focus):
    state=plan();choice=h_plan();choice['focus']=focus
    payload=dict(next_action='experiment',route_reason='A scientific choice',search_plan=choice)
    pure=resolve_intent(state,dict(role='H',step=4),payload)
    parsed=search.interpret(None,dict(role='H',step=4),payload,state=state)
    assert pure['focus'] is None
    assert pure['diagnostics']==parsed['diagnostics']==[dict(code='science_choice_unresolved',subject='focus')]


@pytest.mark.parametrize('index,expected',[(1,'h007.m1'),(2,'h007.m2')])
def test_h_index_maps_new_commitment_in_payload_order(index,expected):
    state=plan();state.update(mechanisms={},authorized=set(),focus=None)
    state['order'].update(remaining=[],allocation=None)
    choice=h_plan(index)
    for item in choice['commitments']:item['existing_ref']=None
    result=search.interpret(None,dict(role='H',step=7),dict(next_action='experiment',
        route_reason='The second prediction is independently observable',search_plan=choice),state=state)
    assert not result['diagnostics']
    assert result['focus']==dict(mechanism_ref=expected,action='screen')
    assert result['allocation']==dict(mechanism_ref=expected,planned_step=7)


def test_same_h_ordinal_is_local_to_each_submitted_commitments_list():
    state=plan();state.update(mechanisms={},authorized=set(),focus=None)
    state['order'].update(remaining=[],allocation=None)
    first_choice=h_plan(1)
    for item in first_choice['commitments']:item['existing_ref']=None
    first=search.interpret(None,dict(role='H',step=7),dict(next_action='experiment',
        route_reason='Test the first authored prediction',search_plan=first_choice),state=state)
    second_choice=copy.deepcopy(first_choice);second_choice['commitments'].reverse()
    second=search.interpret(None,dict(role='H',step=9),dict(next_action='experiment',
        route_reason='Submit independent predictions in a different order',search_plan=second_choice),state=first['state'])
    assert not first['diagnostics'] and not second['diagnostics']
    assert first['focus']==dict(mechanism_ref='h007.m1',action='screen')
    assert second['focus']==dict(mechanism_ref='h009.m1',action='screen')
    assert second['state']['mechanisms']['h009.m1']['label']==second_choice['commitments'][0]['label']=='Q'
    assert second['state']['mechanisms']['h007.m2']['label']=='Q'
    assert second['state']['authorized']=={'h007.m1','h007.m2','h009.m1','h009.m2'}
    assert second['allocation']==dict(mechanism_ref='h009.m1',planned_step=9)
    assert first['state']['current_h']==7 and second['state']['current_h']==9


@pytest.mark.parametrize('index,expected',[(1,'Q'),(2,'P')])
def test_h_reordered_renamed_existing_refs_keep_identity_and_omitted_work(index,expected):
    state=plan();state['mechanisms']['R']=dict(label='Retained omitted mechanism',status='pending')
    state['authorized'].add('R');state['order']['remaining'].append(dict(mechanism_ref='R',planned_step=1))
    before=copy.deepcopy(state)
    choice=h_plan(index,action='improve');choice['commitments'].reverse()
    for item in choice['commitments']:item['label']='Revised '+item['label']
    payload=dict(next_action='experiment',route_reason='Resolve an independent missing condition',search_plan=choice)
    pure=resolve_intent(state,dict(role='H',step=9),payload)
    parsed=search.interpret(None,dict(role='H',step=9),payload,state=state)
    assert pure['focus']==parsed['focus']==dict(mechanism_ref=expected,action='improve')
    assert not pure['diagnostics'] and not parsed['diagnostics']
    assert list(parsed['state']['mechanisms'])==['P','Q','R']
    assert parsed['state']['mechanisms']['P']['label']=='Revised P'
    assert parsed['state']['mechanisms']['Q']['label']=='Revised Q'
    assert parsed['state']['order']['remaining'][-1]==dict(mechanism_ref='R',planned_step=1)
    assert state==before


def test_h_duplicate_labels_still_require_same_output_repair():
    state=plan();choice=h_plan(2)
    choice['commitments'][1]['label']=choice['commitments'][0]['label']
    result=search.interpret(None,dict(role='H',step=4),dict(next_action='experiment',
        route_reason='A scientific choice',search_plan=choice),state=state)
    assert result['diagnostics']==[dict(code='h_mechanism_unresolved',subject='P')]


def test_h_null_screen_keeps_all_retained_work_without_redeclaring_commitments():
    state=plan();before=copy.deepcopy(state)
    result=search.interpret(None,dict(role='H',step=4),dict(next_action='experiment',
        search_plan=dict(focus=dict(index=None,action='screen'))),state=state)
    assert not result['diagnostics']
    assert result['focus']==dict(mechanism_ref='P',action='screen')
    assert result['state']['authorized']=={'P','Q'}
    assert result['state']['order']['remaining']==before['order']['remaining']
    assert state==before


@pytest.mark.parametrize('role,field,key',[('H','search_plan','index'),('A','search_review','mechanism_ref')])
def test_null_focus_only_permits_screen(role,field,key):
    result=resolve_intent(plan(),dict(role=role),dict(next_action='experiment' if role=='H' else 'continue_w',
        **{field:{'focus' if role=='H' else 'next_focus':{key:None,'action':'improve'}}}))
    assert result['focus'] is None
    assert result['diagnostics']==[dict(code='science_focus_required',subject='improve')]


def test_a_requires_exact_ref_and_cannot_resolve_a_display_label():
    state=plan();state['mechanisms']['Q']['label']='Scientific display name'
    payload=dict(next_action='continue_w',route_reason='A scientific choice',
        search_review=dict(next_focus=dict(mechanism_ref='Scientific display name',action='improve')))
    result=resolve_intent(state,dict(role='A'),payload)
    assert result['focus'] is None
    assert result['diagnostics']==[dict(code='science_focus_unauthorized',subject='Scientific display name')]
    payload['search_review']['next_focus']['mechanism_ref']='Q'
    exact=resolve_intent(state,dict(role='A'),payload)
    assert exact['focus']==dict(mechanism_ref='Q',action='improve') and not exact['diagnostics']


def test_a_label_field_cannot_be_treated_as_an_implicit_null_screen():
    payload=dict(next_action='continue_w',search_review=dict(next_focus=dict(label='Q',action='screen')))
    result=resolve_intent(plan(),dict(role='A'),payload)
    assert result['focus'] is None
    assert result['diagnostics']==[dict(code='science_choice_unresolved',subject='focus')]
    with pytest.raises(search.UnresolvedSearch) as error:search._choice(dict(role='A'),payload)
    assert error.value.code=='science_choice_unresolved' and error.value.subject=='next_focus'


def test_w_execution_uses_exact_ref_even_when_another_display_label_matches():
    value=dict(version=1,initial_calls=1,prior_unknown=False,grant=None,
        mechanisms=[dict(mechanism_ref=f'h001.m{index}',label=label,source_h=1,
            source_h_ref=dict(path='original-h-seal'),minimal_test='An independent discriminating measurement',status='pending')
            for index,label in enumerate(('First display title','h001.m1'),1)],
        baseline=[dict(mechanism_ref=f'h001.m{index}',accepted_count=0) for index in (1,2)])
    scope=dict(job_ref=dict(path='original-w-job'))
    before=copy.deepcopy(value)
    with pytest.raises(breadth.OpportunityRequired):breadth.admit(value,'First display title',scope,[])
    binding=breadth.admit(value,'h001.m1',scope,[])
    assert binding==dict(mechanism_ref='h001.m1',source_h=1,source_h_ref=dict(path='original-h-seal'),
        job_ref=scope['job_ref'],grant=None)
    assert value==before


def test_w_exact_ref_must_belong_to_this_frozen_combination():
    first=w_combination('h001.m1');other=w_combination('h003.m1')
    other['mechanisms'][0].update(source_h=3,source_h_ref=dict(path='another-h-seal'))
    scope=dict(job_ref=dict(path='original-w-job'))
    binding=breadth.admit(first,'h001.m1',scope,[])
    assert breadth.check_binding(first,binding,scope)==binding
    with pytest.raises(breadth.OpportunityRequired):breadth.admit(other,'h001.m1',scope,[])
    with pytest.raises(ValueError,match='escaped frozen combination'):breadth.check_binding(other,binding,scope)
    assert breadth.admit(other,'h003.m1',scope,[])['mechanism_ref']=='h003.m1'


@pytest.mark.parametrize('field,replacement',[('source_h',9),('source_h_ref',dict(path='another-h-seal')),
    ('job_ref',dict(path='another-w-job'))])
def test_w_canonical_ref_cannot_rebind_another_h_original_or_w_task(field,replacement):
    value=w_combination();scope=dict(job_ref=dict(path='original-w-job'))
    binding=breadth.admit(value,'h001.m1',scope,[])
    assert breadth.check_binding(value,binding,scope)==binding
    foreign=copy.deepcopy(binding);foreign[field]=replacement
    with pytest.raises(ValueError,match='escaped frozen combination'):breadth.check_binding(value,foreign,scope)


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
