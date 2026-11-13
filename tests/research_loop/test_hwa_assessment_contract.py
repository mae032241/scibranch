"""W direction survives a bounded independent A assessment."""
import copy

import pytest

from scidiscovery.research_loop import contracts as c, decision, search
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from scidiscovery.research_loop.scientific_intent import resolve_intent
from test_scientific_intent import plan


@pytest.mark.parametrize('purpose',[None,'worker_consultation','closeout'])
def test_assessment_and_advice_preserve_supplied_w_choice(purpose):
    state=plan();state['focus']=dict(mechanism_ref='Q',action='validate')
    state['order']['allocation']=dict(mechanism_ref='Q',planned_step=2)
    before=copy.deepcopy(state)
    payload=dict(next_action='continue_w',search_review=dict(
        next_focus=dict(mechanism_ref='P',action='repair'),
        dispositions=[dict(mechanism_ref='Q',status='waived',reason='Authored optional advice')]),
        portfolio_review=dict(main_line='P',discriminating_work=dict(
            mechanism_refs=['P'],question='An optional question',next_action='continue_w')))
    job=dict(role='A',step=3,purpose=purpose)
    result=resolve_intent(state,job,payload)
    parsed=search._interpret_from_facts(None,job,payload,policy=dict(search_policy=search.DEFAULT_POLICY),
        state=state,selected_inputs=[])
    assert result['focus']==before['focus'] and result['allocation']==before['order']['allocation']
    assert result['unfinished_plan']==before['order']['remaining'] and not result['diagnostics']
    assert parsed['state']==before and state==before


@pytest.mark.parametrize('action',['continue_w','revise_h','extract'])
def test_further_research_requires_concrete_question_and_change_criterion_without_io(monkeypatch,action):
    def no_read(*args,**kwargs):raise AssertionError('A request validator must not load evidence')
    monkeypatch.setattr(c,'read',no_read)
    job=dict(role='A')
    with pytest.raises(OutputRepairRequired,match='decision_request'):
        decision.require_request(job,dict(next_action=action,route_reason='Continue because uncertainty remains'))
    request=dict(decision='mechanism',question='Does the missing target-condition observation contradict this claim?',
        change_criterion='A contradiction under the target condition would reopen the assessment.')
    assert decision.require_request(job,dict(next_action=action,decision_request=request))==request


@pytest.mark.parametrize('action',['deliver','finish'])
def test_delivery_assessment_needs_no_fabricated_comparison_or_research_request(action):
    job=dict(role='A')
    payload=dict(next_action=action)
    assert decision.require_request(job,payload) is None
    assert search.require_portfolio_review(None,job,payload) is None


@pytest.mark.parametrize('job',[dict(role='A',purpose='worker_consultation'),
    dict(role='A',purpose='formal_review'),dict(role='A',formal_review={'subject':'exact'}),
    dict(role='A',purpose='closeout')])
def test_advisory_and_protected_requests_keep_their_own_contract(job):
    assert decision.require_request(job,dict(next_action='continue_w')) is None


def test_retained_repair_binds_its_actual_w_author_instead_of_intervening_a(tmp_path,monkeypatch):
    from scidiscovery.research_loop import configuration_updates, handoff_state, history, originals
    state=plan();state['focus']=dict(mechanism_ref='Q',action='repair')
    state['summary']=dict(last_science=dict(role='W',step=2))
    state['order']['diagnostics']=[]
    chooser=dict(role='W',step=2)
    seal=dict(selected_inputs=[])
    selected=[dict(material='exact original selected input')]
    subject=dict(step=1,call='original known failed observation')
    reads=[];choices=[]
    def source(root,step):
        reads.append(step)
        assert step==2
        return chooser,{},seal,dict(path='original-w-seal')
    def repair(root,author,ref,*,selected_inputs):
        choices.append((author,ref,selected_inputs))
        return dict(status='available',root=1,source=subject)
    monkeypatch.setattr(configuration_updates,'for_new_job',lambda *args:dict(search_policy=search.DEFAULT_POLICY))
    monkeypatch.setattr(search,'_current_combination',lambda *args:None)
    monkeypatch.setattr(history,'_source',source)
    monkeypatch.setattr(history,'source_control_ref',lambda *args:dict(path='jobs/002.json'))
    monkeypatch.setattr(originals,'node',lambda *args,**kwargs:object())
    monkeypatch.setattr(originals,'source_record',lambda *args:tmp_path/'original-w-seal')
    monkeypatch.setattr(handoff_state,'selected_materials',lambda *args,**kwargs:selected)
    monkeypatch.setattr(search,'_repair_choice',repair)
    def bounded_breadth(root,state,limits,focus,*,restricted):
        assert restricted is False
        return None
    monkeypatch.setattr(search,'_breadth',bounded_breadth)
    monkeypatch.setattr(c,'record_ref',lambda path:dict(path=str(path)))
    bound=search.bind(tmp_path,4,'W',state=state)
    assert reads==[2] and choices==[(chooser,'Q',selected)]
    assert bound['repair_binding']=='available' and bound['repair_source']==subject
    assert bound['repair_authorization']['step']==2
