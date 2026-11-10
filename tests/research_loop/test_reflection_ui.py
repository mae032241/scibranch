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
        budget_fraction=.35, investment_cutoff=4, policy_version=4, source_w=1,
        cutoff=dict(total_rounds=0, source_step=None, round_id=None),
        due_reasons=['changed_authored_question_or_original_receipt'],
        adaptive_cutoff=dict(question_revision=2, receipts=4),
        budget_scope=dict(tokens=350, seconds=35, limits=dict(tokens=1000, seconds=100),
            complete=dict(tokens=True, seconds=True))), **updates)


def adaptive_request(**updates):
    return request(**updates)


@pytest.mark.parametrize('reason,text', [
    ('changed_authored_question_or_original_receipt', '作者问题或原调用收据的输入边界变化'),
    ('operational_no_new_experiment', '运行检查机会：没有新的实验输入'),
])
def test_adaptive_reflection_renders_control_opportunity_with_safe_scalar_scope(reason, text):
    frozen=adaptive_request(due_reasons=[reason, '/private/thread/auth', 'consecutive_calls'],
        cutoff=dict(total_rounds=0, source_step=None, round_id='/private/source'))
    job=dict(step=2, role='A', research_strategy_version=3, advisory_scope_version=1, supervision_request=frozen)
    before=copy.deepcopy(job)
    projection=ui_semantics.reflection(job)
    assert projection==dict(advisory_scope_version=1,policy_version=4, source_w=1, question_revision=2, receipt_cutoff=4,
        due_reasons=[reason], budget_fraction=0.35, investment_cutoff=4)
    assert 'budget_scope' not in projection and '/private' not in json.dumps(projection)
    node=sealed(2, 'A', dict(summary='Question', next_action='continue_w',
        supervision_result='continue_existing'), research_strategy_version=3, advisory_scope_version=1, supervision_request=frozen)
    reader=FakeReader({2:node})
    line=ui_view.role_line(reader, dict(step=2, role='A', state='seal_present_unverified'), clean)
    markup=ui_components.role_line(line)+ui_components.result_card(ui_view.result_card(reader,2,clean=clean))
    assert text in markup and 'policy 4；控制侧检查机会' in markup and 'W #1' in markup
    assert '不认可或确认当前方向' in markup and 'continue_existing' in markup
    assert '/private' not in markup and 'budget_scope' not in markup
    assert '连续原调用投入达到冻结阈值' not in markup and job==before


def test_adaptive_reflection_requires_complete_typed_frozen_marker_and_ordinary_a_scope():
    job=dict(step=2, role='A', research_strategy_version=3, advisory_scope_version=1, supervision_request=adaptive_request())
    for field in ('policy_version', 'source_w', 'adaptive_cutoff', 'budget_scope', 'cutoff', 'investment_cutoff'):
        partial=copy.deepcopy(job['supervision_request'])
        partial.pop(field)
        assert ui_semantics.reflection(dict(job,supervision_request=partial)) is None
    for changes in (dict(policy_version=4.0), dict(policy_version=True), dict(source_w=True),
            dict(source_w=2), dict(adaptive_cutoff=dict(question_revision=True,receipts=4)),
            dict(budget_scope=dict(tokens=350,seconds=35,limits=dict(tokens=None,seconds=None),
                complete=dict(tokens=True,seconds=True))),
            dict(budget_scope=dict(job['supervision_request']['budget_scope'],private_path='/private/auth'))):
        assert ui_semantics.reflection(dict(job,supervision_request=adaptive_request(**changes))) is None
    for changes in (dict(role='W'),dict(purpose='closeout'),dict(purpose='worker_consultation'),
            dict(formal_review=True),dict(research_strategy_version=2),dict(research_strategy_version=3.0)):
        assert ui_semantics.reflection(dict(job,**changes)) is None
    obsolete=request();obsolete.pop('policy_version')
    assert ui_semantics.reflection(dict(job,supervision_request=obsolete)) is None


def test_adaptive_reflection_preserves_missing_consumption_without_displaying_raw_budget():
    job=dict(step=2,role='A',research_strategy_version=3,advisory_scope_version=1,supervision_request=adaptive_request(
        budget_fraction=None,budget_scope=dict(tokens=None,seconds=None,
            limits=dict(tokens=1000,seconds=None),complete=dict(tokens=False,seconds=False))))
    projection=ui_semantics.reflection(job)
    assert projection['budget_fraction'] is None
    facts=ui_view.reflection_facts(projection)
    assert next(row['value'] for row in facts if row['label']=='请求时预算消费')=='未记录'
    assert 'budget_scope' not in json.dumps(projection) and '0.0%' not in json.dumps(facts)


def test_saved_adaptive_status_projects_frozen_scope_without_raw_budget_or_private_fields(tmp_path,monkeypatch):
    from scidiscovery.research_loop import continuation
    (tmp_path/'native-evidence'/'002').mkdir(parents=True)
    monkeypatch.setattr(continuation,'technical_stop_from_facts',lambda *args,**kwargs:None)
    active=ui_saved_status._node(tmp_path,2,dict(step=2,role='A',research_strategy_version=3,advisory_scope_version=1,
        supervision_request=adaptive_request(due_reasons=['operational_no_new_experiment','/private/auth'])))
    assert active['state']=='prepared' and '自动诊断建议' in active['role_label']
    line=ui_view.role_line(FakeReader({2:None}),active,clean)
    markup=ui_components.role_line(line)
    assert '运行检查机会：没有新的实验输入' in markup and 'policy 4' in markup
    assert 'budget_scope' not in json.dumps(active) and '/private' not in json.dumps(active)




@pytest.mark.parametrize('merged', [False, True])
def test_marked_automatic_a_is_diagnostic_advice_returned_to_w_without_endorsement(merged):
    job=dict(research_strategy_version=3,advisory_scope_version=1,
        supervision_request=adaptive_request())
    if merged:
        job.update(purpose='worker_consultation',evidence_return=dict(role='W',step=1),
            consultation_question='Which counterevidence changes the interpretation?')
    payload=dict(summary='No material issue identified.',scientific_result='No new constraint; W chooses its next work.',
        next_action='continue_w',decision_request=None,supervision_result='continue_existing',candidate_recommendation=None)
    node=sealed(2,'A',payload,**job)
    before=copy.deepcopy(node)
    reader=FakeReader({2:node})
    projection=ui_semantics.reflection(node['job'])
    assert projection['advisory_scope_version']==1 and projection['policy_version']==4
    assert 'budget_scope' not in projection
    line=ui_view.role_line(reader,dict(step=2,role='A',state='seal_present_unverified'),clean)
    card=ui_view.result_card(reader,2,clean=clean)
    markup=ui_components.role_line(line)+ui_components.result_card(card)
    assert '自动诊断建议' in markup and '不认可或确认当前方向' in markup
    assert 'continue_existing 仅表示回程' in markup and '继续现有研究（continue_existing）' not in markup
    assert 'W #1' in markup and '触发原因' in markup and '35.0%' in markup
    assert card['chosen'] is None and card['delivery_state']=='consultation'
    assert not any(row['label']=='决定' for row in line['facts'])
    assert not any(row['label']=='候选推荐' for row in line['facts'])
    assert ui_semantics.kind(node['job'])==('consultation' if merged else 'ordinary')
    assert node==before


def test_marked_scope_requires_exact_integer_and_keeps_final_a_separate():
    ordinary=dict(step=2,role='A',research_strategy_version=3,advisory_scope_version=1)
    assert ui_semantics.role_label(ordinary)=='A · 研究诊断建议（返回 W）'
    for changes in (dict(advisory_scope_version=None),dict(advisory_scope_version=True),
            dict(advisory_scope_version=1.0),dict(research_strategy_version=2),
            dict(research_strategy_version=True)):
        assert not ui_semantics.diagnostic_advice(dict(ordinary,**changes))
    for changes in (dict(purpose='closeout'),dict(formal_review=True),dict(engineering_repair=True)):
        assert not ui_semantics.diagnostic_advice(dict(ordinary,**changes))
        assert '诊断建议' not in ui_semantics.role_label(dict(ordinary,**changes))
    final=dict(ordinary,evidence_return=dict(kind='delivery_review',step=1,corrections_used=0))
    assert ui_semantics.role_label(final)=='A · 提交前最终评估' and ui_semantics.assessment(final)
    assert ui_semantics.action(final,'deliver',ui_view.A_OUTCOMES)=='交付'
    merged=dict(ordinary,purpose='worker_consultation',supervision_request=adaptive_request())
    for return_binding in (None,dict(role='H',step=1),dict(role='W',step=True),dict(role='W',step=2)):
        assert ui_semantics.reflection(dict(merged,evidence_return=return_binding)) is None


def test_prepared_merged_consultation_shows_safe_automatic_facts_without_private_bindings(tmp_path,monkeypatch):
    from scidiscovery.research_loop import continuation
    (tmp_path/'native-evidence'/'002').mkdir(parents=True)
    monkeypatch.setattr(continuation,'technical_stop_from_facts',lambda *args,**kwargs:None)
    job=dict(step=2,role='A',research_strategy_version=3,advisory_scope_version=1,
        purpose='worker_consultation',evidence_return=dict(role='W',step=1),
        supervision_request=adaptive_request(due_reasons=['operational_no_new_experiment','/private/auth']),
        consultation_question='Bounded scientific question',private_binding='/private/thread',auth='secret')
    active=ui_saved_status._node(tmp_path,2,job)
    line=ui_view.role_line(FakeReader({2:None}),active,clean)
    markup=ui_components.role_line(line)
    assert active['state']=='prepared' and 'W 咨询与自动诊断建议' in markup
    assert '运行检查机会：没有新的实验输入' in markup and '不认可或确认当前方向' in markup
    assert '/private' not in json.dumps(active) and 'secret' not in json.dumps(active)
    assert 'budget_scope' not in json.dumps(active)


def test_marked_e_consultation_displays_evidence_help_returning_to_w():
    node=sealed(2,'E',dict(summary='Evidence with limits.',scientific_result='Bounded evidence answer.',
        next_action='continue_w'),purpose='worker_consultation',research_strategy_version=3,
        advisory_scope_version=1,evidence_return=dict(role='W',step=1))
    reader=FakeReader({2:node})
    line=ui_view.role_line(reader,dict(step=2,role='E',state='seal_present_unverified'),clean)
    card=ui_view.result_card(reader,2,clean=clean)
    markup=ui_components.role_line(line)+ui_components.result_card(card)
    assert line['role_label']=='E · W 证据咨询'
    assert '证据答复后返回原 W' in markup and '由 W 判断后续研究' in markup
    assert card['chosen'] is None and card['delivery_state']=='consultation'


@pytest.mark.parametrize('merged', [False, True])
def test_diagnostic_advice_cannot_display_selection_criteria_as_authorized_change(merged):
    explanation=dict(criteria='A selected criteria',criteria_change=dict(status='revised',
        prior_criteria='Original criteria',changes='A requested a new criterion',
        evidence='A supplied evidence',accepted_tradeoffs='A tradeoff'))
    payload=dict(summary='Diagnosis only.',scientific_result='Advice remains in the original.',
        next_action='continue_w',decision_request=None,route_reason='Advice returned to W.',
        selection_explanation=explanation)
    job=dict(research_strategy_version=3,advisory_scope_version=1)
    if merged:
        job.update(purpose='worker_consultation',evidence_return=dict(role='W',step=1))
    node=sealed(2,'A',payload,**job)
    before=copy.deepcopy(node)
    card=ui_view.result_card(FakeReader({2:node}),2,clean=clean)
    assert card['criteria_change'] is None and card['reason']=='Advice returned to W.'
    assert '准则已修订' not in ui_components.result_card(card)
    assert node==before
    final=sealed(2,'A',dict(payload,next_action='finish'),research_strategy_version=3,
        advisory_scope_version=1,evidence_return=dict(kind='delivery_review',step=1,corrections_used=0))
    final_card=ui_view.result_card(FakeReader({2:final}),2,clean=clean)
    assert final_card['criteria_change']['label']=='准则已修订'
    assert final_card['reason']=='A selected criteria'




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
    active=ui_saved_status._node(tmp_path,2,dict(step=2,role='A',research_strategy_version=3,
        advisory_scope_version=1,supervision_request=request(private_ref='/private/request')))
    assert len(calls)==2 and '研究诊断建议' in active['role_label']
    assert '返回 W' in active['role_label']
    assert '/private' not in json.dumps(active)
    line=ui_view.role_line(FakeReader({2:None}),active,clean)
    markup=ui_components.role_line(line)
    assert '研究诊断建议' in markup and '返回 W' in markup
    assert '/private' not in markup and 'secret' not in markup
