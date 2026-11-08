"""Reflection UI consumes frozen facts without creating scientific authority."""
import copy
import json

import pytest

from scidiscovery.research_loop import ui_components, ui_saved_status, ui_semantics, ui_view
from test_ui_view import FakeReader, sealed


def clean(value, limit=16384):
    return str(value)[:limit]


def request(**updates):
    return dict(dict(version=1, review_kind='ordinary_reflection',
        reflection_scope='worker_question_clues_evidence', automatic=True, final=False,
        due_reasons=['consecutive_calls', 'budget_checkpoint:0.3'],
        budget_fraction=0.35, investment_cutoff=4), **updates)


def test_only_explicit_ordinary_automatic_marker_changes_label_and_preserves_authority():
    job=dict(step=2, role='A', research_strategy_version=3, supervision_request=request())
    assert ui_semantics.role_label(job)=='A · 自动研究反思'
    assert ui_semantics.kind(job)=='ordinary' and ui_semantics.assessment(job)
    assert ui_semantics.action(job,'finish',ui_view.A_OUTCOMES)=='请求提交前最终评估'
    for changes in (dict(automatic=False), dict(final=True), dict(review_kind='final_assessment'),
            dict(reflection_scope='other'), dict(version=True)):
        assert ui_semantics.role_label(dict(job,supervision_request=request(**changes))) is None
    for category in (dict(purpose='closeout'), dict(formal_review=True)):
        assert '自动研究反思' not in ui_semantics.role_label(dict(job,**category))
    legacy=dict(job,supervision_request={})
    assert ui_semantics.role_label(dict(legacy,purpose='worker_consultation'))=='A · W 研究咨询'
    assert ui_semantics.role_label(dict(job,purpose='worker_consultation'))=='A · W 研究咨询'


def test_reflection_projection_keeps_known_consumption_facts_and_actual_author_action():
    job=dict(step=2,role='A',research_strategy_version=3,supervision_request=request(
        due_reasons=['consecutive_calls','budget_checkpoint:0.3','/private/session',{},'budget_checkpoint:nan']))
    before=copy.deepcopy(job)
    projection=ui_semantics.reflection(job)
    assert projection['due_reasons']==['consecutive_calls','budget_checkpoint:0.3']
    assert '/private' not in json.dumps(projection)
    assert ui_semantics.kind(job)=='ordinary' and ui_semantics.assessment(job)
    for action in ('continue_existing','wait'):
        payload=dict(summary='W question',next_action='continue_w' if action=='continue_existing' else action)
        if action=='continue_existing':payload['supervision_result']='continue_existing'
        node=sealed(2,'A',payload,research_strategy_version=3,supervision_request=job['supervision_request'])
        reader=FakeReader({2:node})
        line=ui_view.role_line(reader,dict(step=2,role='A',state='seal_present_unverified'),clean)
        markup=ui_components.role_line(line)
        assert '自动研究反思' in markup and '预算检查点 30%' in markup and '35.0%' in markup
        assert ('continue_existing' if action=='continue_existing' else '等待条件') in markup
        assert not any(row['label']=='候选推荐' for row in line['facts'])
        card=ui_view.result_card(reader,2,clean=clean)
        assert '自动研究反思' in ui_components.result_card(card)
    assert job==before


def test_clues_without_candidates_keep_scope_exact_evidence_and_source_pointer():
    source=dict(source_step=1,source_role='W')
    clue=dict(observed_fact='Observed <signal>',applicability='Only this split',
        limited_negative='No signal in this split',open_question='Other input?',
        source=dict(source,pointer='/research_memory/clues/3'),
        evidence=[dict(ref='inputs/history/nodes/001/science.json',relation='supports',note='Observed trace'),
            dict(ref={'path':'inputs/parents/001/artifacts/trace.csv'},relation='opposes',note='Limited negative'),
            dict(ref='/private/session',relation='unknown',note='Still unknown')])
    memory=dict(source=source,question='Which evidence changes the question?',candidates=[],clues=[clue])
    node=dict(research=dict(candidate_memory=memory))
    loaded=dict(job=dict(step=1),seal=dict(materials=[dict(path='inputs/parents/001/artifacts/trace.csv')]))
    before=copy.deepcopy(node)
    value=ui_view.candidate_memory(node,clean,loaded_nodes=[loaded])
    markup=ui_components.candidate_memory(value)
    for text in ('研究线索','Only this split','No signal in this split','Other input?',
            '/research_memory/clues/3','/nodes/1/science','/nodes/1/materials/1','支持','反对','未知'):
        assert text in markup
    assert 'Observed &lt;signal&gt;' in markup and '<signal>' not in markup
    assert '/private/session' not in markup and node==before
    assert '有限否定只适用于声明范围' in markup


def test_clue_display_budget_preserves_complete_html_and_original_navigation():
    source=dict(source_step=1,source_role='W')
    clue=dict(observed_fact='&'*600,applicability='&'*600,limited_negative='&'*600,
        open_question='&'*600,source=dict(source,pointer='/research_memory/clues/0'),
        evidence=[dict(ref='inputs/history/nodes/001/science.json',relation='unknown',note='&'*400)]*8)
    value=ui_view.candidate_memory(dict(research=dict(candidate_memory=dict(source=source,
        question='&'*1200,candidates=[],clues=[clue]*40))),clean)
    markup=ui_components.candidate_memory(value)
    assert len(markup.encode('utf-8'))<65536
    assert markup.count('<article')==markup.count('</article>')
    assert markup.count('<ul>')==markup.count('</ul>')
    assert markup.endswith('</section>') and '/nodes/1/science' in markup and '显示预算' in markup


@pytest.mark.parametrize('mode',['same_session','new_context','resume_unconfirmed'])
def test_continuation_uses_observed_outcome_tuple_and_never_planned_resume(mode):
    frozen=dict(version=1,requester_step=1,advice_step=2,private_ref='/private/original')
    job=dict(step=3,role='W',worker_continuity_version=1,worker_continuation=frozen)
    outcome=dict(worker_continuation=dict(version=1,requester_step=1,advice_step=2,
        mode=mode,fallback_reason=None if mode!='new_context' else 'consultation_resume_unsupported_backend'),
        native_session='/private/session',auth='secret')
    before=copy.deepcopy(outcome)
    value=ui_semantics.worker_continuation(job,outcome)
    reader=FakeReader({3:None})
    line=ui_view.role_line(reader,dict(step=3,role='W',state='running',worker_continuation=value),clean)
    markup=ui_components.role_line(line)
    assert ('已观察到原会话接续' in markup)==(mode=='same_session')
    assert ('实际恢复尚未证实' in markup)==(mode=='resume_unconfirmed')
    assert ('已使用新上下文接续' in markup)==(mode=='new_context')
    assert '/private' not in markup and 'secret' not in markup and outcome==before
    assert ui_semantics.worker_continuation(job,dict(worker_continuation=dict(mode='resume_requested'))) is None
    for changes in (dict(requester_step=True),dict(advice_step=3),dict(requester_step=2),dict(version=True)):
        assert ui_semantics.worker_continuation(job,dict(worker_continuation=dict(outcome['worker_continuation'],**changes))) is None
    assert ui_semantics.worker_continuation(dict(job,worker_continuation=dict(frozen,requester_step=2)),outcome) is None
    assert ui_semantics.worker_continuation(dict(job,worker_continuity_version=None),outcome) is None
    assert ui_semantics.worker_continuation(dict(job,step=4),outcome) is None


@pytest.mark.parametrize('suffix,label',[
    ('backend_changed','执行后端已变化'),
    ('context_replaced','原接续已明确替换上下文'),
])
def test_backend_change_and_context_replacement_show_explicit_fallback(suffix,label):
    frozen=dict(version=1,requester_step=1,advice_step=2)
    job=dict(step=3,role='W',worker_continuity_version=1,worker_continuation=frozen)
    value=ui_semantics.worker_continuation(job,dict(worker_continuation=dict(frozen,
        mode='new_context',fallback_reason='consultation_resume_'+suffix)))
    facts=ui_view.worker_continuation_facts(value)
    assert any(label in row['value'] for row in facts)
    assert not any('已观察到原会话接续' in row['value'] for row in facts)


def test_saved_status_projects_loaded_outcome_once_without_private_fields(tmp_path,monkeypatch):
    from scidiscovery.research_loop import continuation
    calls=[]
    outcome=dict(worker_continuation=dict(version=1,requester_step=1,advice_step=2,
        mode='new_context',fallback_reason='/private/session'),auth='secret')
    monkeypatch.setattr(ui_saved_status,'attempts',lambda root,step:[tmp_path/'record'])
    monkeypatch.setattr(ui_saved_status,'_native_outcome',lambda record,memo:(calls.append(record) or outcome))
    monkeypatch.setattr(ui_saved_status,'_read',lambda path,default=None:default)
    monkeypatch.setattr(continuation,'technical_stop_from_facts',lambda *args,**kwargs:None)
    node=ui_saved_status._node(tmp_path,3,dict(step=3,role='W',worker_continuity_version=1,
        worker_continuation=dict(version=1,requester_step=1,advice_step=2,private_ref='/private/original')))
    assert len(calls)==1 and node['worker_continuation']['mode']=='new_context'
    assert node['worker_continuation']['fallback_reason'] is None
    assert '/private' not in json.dumps(node) and 'secret' not in json.dumps(node)
    active=ui_saved_status._node(tmp_path,2,dict(step=2,role='A',supervision_request=request(
        private_ref='/private/request')))
    assert len(calls)==2 and active['role_label']=='A · 自动研究反思'
    assert '/private' not in json.dumps(active)
    line=ui_view.role_line(FakeReader({2:None}),active,clean)
    assert '预算检查点 30%' in ui_components.role_line(line)
