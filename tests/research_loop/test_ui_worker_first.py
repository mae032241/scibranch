"""UI consumes frozen W-first owner facts without inventing authority."""
import copy
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import experiments, instance_ui, ui_components, ui_mechanisms, ui_saved_status, ui_semantics, ui_view, worker_research
from test_ui_view import FakeReader, sealed


def clean(value,limit=16384):
    return worker_research.public_text(str(value),limit) or ''


def owner(tmp_path):
    root=tmp_path
    job=dict(step=1,role='W',handoff_protocol=33,worker_view_version=1,
        lineage_projection_version=1,research_strategy_version=3,search=dict(breadth=dict(mechanisms=[])))
    scope=dict(step=1,role='W',handoff_protocol=33,job_ref=dict(path=str(root/'jobs/001.json')),
        execution_key=dict(path=str(root/'jobs/001.json')),mechanism_ref=None)
    records=root/'host-runtime/experiment-control/001/records';records.mkdir(parents=True)
    config=dict(record_dir=str(records),scope=scope,research_strategy_version=3,
        breadth=dict(mechanisms=[]),worker_research_goal_id='ui-fixture')
    return root,job,config,experiments.Records(config)


def declared(config):
    state=worker_research.initial(config)
    return worker_research.evolve(state,[dict(action='declare',mechanism_ref='w001.m1',
        question='Which controlled observation separates the alternatives?',exploration=True)],
        scope=config['scope'],original_ref=dict(path=str(Path(config['record_dir'])/'research/000001.json')))


def accepted(config,state,call='call-1'):
    state,binding=worker_research.prepare(config,state,dict(mechanism_ref='w001.m1',experiment_question=dict(
        question='Does the first input expose the observable?',change_criterion='Change inputs if the observable is absent.')))
    return dict(scope=config['scope'],call_id=call,mechanism_binding=binding,native_attempt=dict(number=0),
        code_version=dict(entry='observe.py'),_display_first_call=True)


def test_original_call_revision_stays_frozen_when_same_w_revises_without_a_call(tmp_path):
    root,job,config,records=owner(tmp_path)
    first=declared(config);call=accepted(config,first)
    records._research_prior_calls=[];records._research_state=first
    records.live_status('call-1','running',accepted=call)
    second=worker_research.evolve(first,[dict(action='revise',mechanism_ref='w001.m1',question='A revised mechanism question')],
        scope=config['scope'],original_ref=dict(path=str(Path(config['record_dir'])/'research/000002.json')))
    records.research_status(second)
    projected=ui_saved_status._live_executions(root,job)
    display=projected['calls'][0]['display']
    assert display['research_binding']['revision']==1
    assert display['research_binding']['question']==first['mechanisms']['w001.m1']['question']
    assert display['purpose']=='Does the first input expose the observable?'
    assert display['change_criterion']=='Change inputs if the observable is absent.'
    assert projected['research']['revision']==2
    assert projected['research_investment']['total_calls']==1
    html=instance_ui.InstanceUI._execution_activity(projected)
    assert 'revision 1' in html and 'W 已声明机制账本' in html
    ledger=ui_mechanisms.render(ui_mechanisms.project(None,[],clean,live_executions=projected))
    assert 'A revised mechanism question' in ledger and 'revision 2' in ledger
    assert first['mechanisms']['w001.m1']['question'] in html
    assert '原调用总数：1' in html and '当前连续投入：1' in html
    assert '科学轮次、研究深度或科学认可' in html
    assert str(root) not in html and 'job_ref' not in json.dumps(projected)


def test_no_call_declaration_uses_same_view_missing_investment_stays_unknown(tmp_path):
    root,job,config,records=owner(tmp_path)
    assert ui_saved_status._live_executions(root,job)['coverage']=='unknown'
    records.research_status(declared(config))
    value=ui_saved_status._live_executions(root,job)
    assert value['calls']==[] and value['research_investment'] is None
    assert value['research']['mechanisms'][0]['mechanism_ref']=='w001.m1'
    html=instance_ui.InstanceUI._execution_activity(value)
    assert '缺失视图不表示零投入' in html and 'W 已声明机制账本' in html
    ledger=ui_mechanisms.render(ui_mechanisms.project(None,[],clean,live_executions=value))
    assert 'w001.m1' in ledger and 'Which controlled observation separates the alternatives?' in ledger
    path=Path(config['record_dir'])/'live-status.json'
    raw=json.loads(path.read_text());raw['scope']['execution_key']={'path':'/foreign/jobs/001.json'}
    path.write_text(json.dumps(raw))
    assert ui_saved_status._live_executions(root,job)['coverage']=='unknown'


def test_live_markup_budget_omits_complete_rows_after_html_escaping():
    display=dict(entry='&'*160,purpose='&'*240,mechanism='&'*160,change_criterion='&'*240,
        research_binding=dict(mechanism_ref='w001.m1',revision=1,question='&'*240),
        metrics=[dict(name='&'*80,value=1,unit='&'*40) for _ in range(3)])
    value=dict(step=1,coverage='observed',counts=dict(running=20),limitation='Owner observations only',
        calls=[dict(call_id='call-'+str(index),state='running',attempt=0,updated_at=1.,display=display)
            for index in range(20)])
    html=instance_ui.InstanceUI._execution_activity(value)
    assert len(html.encode('utf-8'))<=65536
    assert '因显示长度限制未展开' in html
    assert html.count('<details')==html.count('</details>')
    assert html.count('<tr>')==html.count('</tr>') and html.endswith('</details>')


def test_navigation_published_during_missing_read_is_not_cached_as_absent(tmp_path,monkeypatch):
    from scidiscovery.research_loop import instance_display
    (tmp_path/'jobs').mkdir();record=tmp_path/'native-evidence/001';record.mkdir(parents=True)
    origin=dict(goal_id='navigation-race',settings_ref=dict(path=str(tmp_path/'settings.json')))
    job=dict(step=1,role='W',research_strategy_version=3,history=dict(prefix=dict(version=1,
        branch=origin,visibility_root=dict(path='host-history/accepted-public/navigation-race'))))
    for path,value in ((tmp_path/'settings.json',{}),(tmp_path/'jobs/001.json',job),
        (record/'result-identity.json',dict(materials=[])),(record/'result.json',dict(summary='Sealed')),
        (record/'science-output.json',dict(summary='Sealed',scientific_result='Original science',limitations=[]))):
        path.write_text(json.dumps(value))
    navigation=instance_display._navigation_file(tmp_path,job)
    published=dict(step=1,role='W',research=dict(candidate_memory=dict(source=dict(source_step=1,source_role='W'),
        question='Published after sealing',candidates=[])))
    original=instance_display._json
    def publish_after_missing(path):
        if path==navigation and not path.exists():
            navigation.parent.mkdir(parents=True)
            navigation.write_text(json.dumps(published))
            raise ValueError('Navigation was absent at this read')
        return original(path)
    monkeypatch.setattr(instance_display,'_json',publish_after_missing)
    memo=ui_view.DisplayMemo()
    assert ui_view.Reader(tmp_path,memo).node(1)['research'] is None
    assert (str(tmp_path),1) not in memo
    refreshed=ui_view.Reader(tmp_path,memo).node(1)
    assert refreshed['research']['candidate_memory']['question']=='Published after sealing'


@pytest.mark.parametrize('value',[None,dict(version=1,scope={},total_calls=True,consecutive_calls=0,by_mechanism={})])
def test_investment_validation_never_promotes_missing_or_malformed_values(value):
    assert ui_saved_status._public_investment(value,{}) is None


def test_inherited_investment_counts_can_overlap_without_double_counting_total():
    value=dict(version=1,scope={},total_calls=2,consecutive_calls=2,
        by_mechanism={'w001.parent':2,'w001.child':2},direction='w001.child')
    shown=ui_saved_status._public_investment(value,{})
    assert shown['total_calls']==2 and [row['calls'] for row in shown['by_mechanism']]==[2,2]
    value['by_mechanism']['w001.child']=3
    assert ui_saved_status._public_investment(value,{}) is None


@pytest.mark.parametrize('strategy',[None,1,3])
def test_terminal_selection_preserves_legacy_and_requires_real_final_a(strategy):
    job=dict(role='A',step=3,research_strategy_version=strategy)
    terminal=instance_ui.InstanceUI._ordinary_assessment(job)
    conclusion=dict(step=3,ordinary=terminal,payload=dict(next_action='finish'))
    saved=dict(result_step=None,closeout=None)
    expected=3 if strategy in (None,1) else None
    assert instance_ui.InstanceUI._selected_result(saved,3,conclusion)==expected
    assert instance_ui.InstanceUI._selected_result(saved,4,conclusion) is None
    consult=dict(job,purpose='worker_consultation')
    assert not ui_semantics.terminal_finish(consult)
    if strategy in (2,3):
        final=dict(job,evidence_return=dict(kind='delivery_review',step=2,corrections_used=0))
        assert ui_semantics.terminal_finish(final)
        assert ui_semantics.action(job,'finish',ui_view.A_OUTCOMES)=='请求提交前最终评估'
        assert ui_semantics.action(final,'finish',ui_view.A_OUTCOMES)=='结束探索'


def test_consultation_does_not_decide_round_and_delivery_review_has_exact_label():
    reader=FakeReader({1:sealed(1,'W',dict(summary='Observed')),
        2:sealed(2,'A',dict(summary='Advice',next_action='finish',candidate_recommendation='unavailable'),
            purpose='worker_consultation',research_strategy_version=3),
        3:sealed(3,'A',dict(summary='Decision request',next_action='deliver'),research_strategy_version=3)})
    nodes=[dict(step=step,role=reader.node(step)['job']['role'],state='seal_present_unverified') for step in (1,2,3)]
    item,=ui_view.rounds(reader,nodes,clean)['rounds']
    assert len(item['a'])==1 and item['others'][0]['kind']=='consultation'
    assert item['outcome']=='请求提交前最终评估'
    assert not any(row['label']=='候选推荐' for row in item['others'][0]['facts'])
    assert ui_semantics.role_label(dict(role='W',purpose='closeout',engineering_repair={"enabled":True}))=='W · 工程维修'


@pytest.mark.parametrize('advice',[None,'local-b'])
def test_consultation_summary_and_candidate_projection_preserve_the_real_a_choice(tmp_path,monkeypatch,advice):
    candidates=[dict(id=identity,label=identity,implementation=None,evaluation_key='declared',metric=None,
        direction=None,status='unknown',evidence=[]) for identity in ('local-a','local-b')]
    observation=dict(candidates=candidates,current_candidate=None,actual_submission=None,
        managed_calls=[],coverage=dict(status='partial'))
    nodes={1:sealed(1,'W',dict(summary='Observed'),observation=observation),
        2:sealed(2,'A',dict(summary='Assess',next_action='wait',candidate_recommendation='local-a')),
        3:sealed(3,'A',dict(summary='Consult',next_action='finish',candidate_recommendation=advice),
            purpose='worker_consultation',research_strategy_version=3)}
    reader=FakeReader(nodes);reader.root=tmp_path
    monkeypatch.setattr(ui_view,'resolve_candidate',lambda root,job,value,*args,**kwargs:value)
    summary_reader=ui_view.Reader(tmp_path)
    monkeypatch.setattr(summary_reader,'node',reader.node)
    monkeypatch.setattr(summary_reader,'observation',reader.observation)
    assert summary_reader.summary(2)['recommendation']=='local-a'
    assert 'recommendation' not in summary_reader.summary(3)
    projection=ui_view.candidate_projection(tmp_path,4,reader=reader,
        job=dict(role='W',step=4,history=dict(nodes=[dict(step=step) for step in (1,2,3)])))
    assert projection['recommended']=='local-a'
    card=ui_view.result_card(reader,3,projection=projection,clean=clean)
    assert card['kind']=='consultation' and card['chosen'] is None
    assert card['recommendation_raw'] is None
    markup=ui_components.result_card(card)
    assert '研究咨询' in markup and '咨询不产生正式候选推荐' in markup


def test_consultation_audit_text_never_becomes_an_audit_request():
    reader=FakeReader({3:sealed(3,'A',dict(summary='Consultation',next_action='audit',candidate_recommendation='local-a'),
        purpose='worker_consultation',research_strategy_version=3)})
    card=ui_view.result_card(reader,3,clean=clean)
    assert card['delivery_state']=='consultation' and card['audit_target'] is None
    assert card['decision']=='咨询答复后返回原 W；本建议不决定路由'


@pytest.mark.parametrize('strategy',[3])
def test_paused_ordinary_a_finish_is_not_completed_but_final_a_finish_is(strategy):
    ui=object.__new__(instance_ui.InstanceUI)
    job=dict(step=3,role='A',research_strategy_version=strategy)
    conclusion=dict(step=3,ordinary=ui_semantics.terminal_finish(job),payload=dict(next_action='finish'))
    saved=dict(result_step=None,closeout=None)
    snapshot=dict(view=dict(nodes=[dict(step=3,role='A',state='seal_present_unverified')],paused=True),
        conclusion=conclusion,final=ui._selected_result(saved,3,conclusion))
    assert not ui._presentation(snapshot,{})['completed']
    final=dict(job,evidence_return=dict(kind='delivery_review',step=2,corrections_used=0))
    conclusion['ordinary']=ui_semantics.terminal_finish(final)
    snapshot['final']=ui._selected_result(saved,3,conclusion)
    assert ui._presentation(snapshot,{})['completed']


def test_consultation_wait_does_not_put_paused_research_into_scientific_wait():
    ui=object.__new__(instance_ui.InstanceUI)
    snapshot=dict(view=dict(nodes=[dict(step=2,role='A',kind='consultation',state='seal_present_unverified')],paused=True),
        conclusion=dict(step=2,payload=dict(next_action='wait')),final=None)
    presentation=ui._presentation(snapshot,{})
    assert not presentation['waiting'] and not presentation['completed']
    assert presentation['title']=='研究已暂停'


def test_sealed_v3_keeps_exact_call_revision_and_no_call_declaration_findings(tmp_path):
    _,_,config,_=owner(tmp_path)
    state=declared(config);call=accepted(config,state)
    next_state=worker_research.evolve(state,[dict(action='revise',mechanism_ref='w001.m1',question='Revised question'),
        dict(action='declare',mechanism_ref='w001.no_call',question='A declared direction with no invocation')],
        scope=config['scope'],original_ref=dict(path=str(Path(config['record_dir'])/'research/000002.json')))
    observation=dict(managed_calls=[dict(call_id='call-1',accepted={'proof':1},status='known_failed',
        mechanism_binding=call['mechanism_binding'])],candidates=[],research_state=next_state)
    node=sealed(1,'W',dict(summary='Scoped findings',mechanism_findings=[
        dict(mechanism='w001.m1',basis='observed',finding='The observable was absent.'),
        dict(mechanism='w001.no_call',basis='blocked',finding='No independent input was available.')]),
        observation=observation,research_strategy_version=3,search=dict(breadth=dict(mechanisms=[])))
    before=copy.deepcopy(node)
    projected=ui_mechanisms.project(FakeReader({1:node}),[1],clean)
    rendered=ui_mechanisms.render(projected)
    assert node==before
    assert len(projected['mechanisms'])==2
    assert 'revision 1' in rendered and 'revision 2' in rendered
    assert state['mechanisms']['w001.m1']['question'] in rendered and 'Revised question' in rendered
    assert 'The observable was absent.' in rendered and 'No independent input was available.' in rendered
    assert str(tmp_path) not in rendered and 'source_scope' not in rendered


def test_accepted_candidate_memory_renders_relationships_without_nomination_or_recommendation():
    node=dict(research=dict(candidate_memory=dict(source=dict(source_step=1,source_role='W'),
        question='Which failure changes the choice?',candidates=[dict(candidate='W001-C001/artifacts/model',
        data='Declared split',evaluation='Author comparison',failure_condition='Missing observation',open_question='New input?',
        limitations=['No independent final sample'],evidence=[dict(ref='inputs/parents/001/artifacts/model',
            relation=relation,note='Conditional '+relation) for relation in ('supports','opposes','unknown')])])),
        job=dict(step=2,role='A'),seal=dict(materials=[]))
    value=ui_view.candidate_memory(node,clean)
    rendered=ui_components.candidate_memory(value)
    for text in ('Declared split','Author comparison','Missing observation','New input?','No independent final sample','支持','反对','未知'):
        assert text in rendered
    assert '/nodes/1/science' in rendered and '/nodes/1/materials' in rendered
    assert '与提名、A 推荐、候选资格分别记录' in rendered
    assert '未提供已接受' in ui_components.candidate_memory(ui_view.candidate_memory({},clean))


def test_inherited_and_local_same_raw_candidate_keep_exact_materials_and_recommendation(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from scidiscovery.research_loop import candidate_refs,instance_display,originals
    parent=tmp_path/'parent';child=tmp_path/'child';parent.mkdir();child.mkdir()
    origin=dict(goal_id='parent-fixture',settings_ref=dict(path=str(parent/'settings.json')))
    source=SimpleNamespace(original_root=str(parent),origin_identity=origin,step=1)
    observation=lambda step:dict(candidates=[dict(id='model',label='Candidate '+str(step),
        implementation=f'history/nodes/{step:03d}/artifacts/model.py',evaluation_key='declared',metric=None,
        direction=None,status='unknown',evidence=[])],current_candidate=None,actual_submission=None,
        coverage=dict(status='partial'))
    documents={};seals={};jobs={}
    for root,step in ((parent,1),(child,2)):
        record=root/f'native-evidence/{step:03d}';record.mkdir(parents=True)
        material=root/f'project/nodes/{step:03d}/artifacts/model.py'
        material.parent.mkdir(parents=True);material.write_text(str(step));documents[material]=material.read_bytes()
        jobs[step]=dict(step=step,role='W',research_strategy_version=3,history={})
        seals[step]=dict(observations=dict(path=f'host-history/observations/{step:03d}.json'),materials=[dict(
            path=f'inputs/parents/{step:03d}/artifacts/model.py',source=str(material),size_bytes=1)])
        payload=dict(summary='Retained '+str(step),scientific_result='Fixture observation',limitations=[])
        for relative,value in ((f'jobs/{step:03d}.json',jobs[step]),
            (f'host-history/observations/{step:03d}.json',observation(step)),
            (f'native-evidence/{step:03d}/result.json',payload),
            (f'native-evidence/{step:03d}/science-output.json',payload)):
            path=root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));documents[path]=path.read_bytes()
    monkeypatch.setattr(instance_display,'_saved',lambda root,step:(child,source if step==1 else 'native-evidence/002',
        seals[step],(parent if step==1 else child)/f'native-evidence/{step:03d}/science-output.json'))
    monkeypatch.setattr(originals,'source_job',lambda exact:jobs[1])
    monkeypatch.setattr(originals,'source_record',lambda exact,name:parent/'native-evidence/001'/name)
    monkeypatch.setattr(originals,'resolve_ref',lambda exact,ref:parent/ref['path'])
    acquired=[];loader=candidate_refs.source_input_mapping
    def input_mapping(exact,job):
        acquired.append(exact);return loader(exact,job)
    monkeypatch.setattr(candidate_refs,'source_input_mapping',input_mapping)
    inherited=instance_display.node(child,1);local=instance_display.node(child,2)
    before=instance_display._json
    monkeypatch.setattr(instance_display,'_json',lambda path:pytest.fail('Loaded observation consumer reopened a record'))
    inherited_observation=instance_display.observation(child,1,node=inherited)
    monkeypatch.setattr(instance_display,'_json',before)
    parent_id=candidate_refs.origin_candidate(origin,'model')
    assert inherited_observation['candidates'][0]['id']==parent_id and acquired==[source]
    inherited['observation']=inherited_observation
    local['observation']=instance_display.observation(child,2,node=local)
    assessment=sealed(3,'A',dict(summary='Exact parent choice',next_action='wait',candidate_recommendation=parent_id))
    reader=FakeReader({1:inherited,2:local,3:assessment});reader.root=child
    projection=ui_view.candidate_projection(child,4,reader=reader,
        job=dict(step=4,role='W',history=dict(nodes=[dict(step=step) for step in (1,2,3)])))
    indexed={row['id']:row for row in projection['index']}
    assert set(indexed)=={parent_id,'model'} and projection['recommended']==parent_id
    assert indexed[parent_id]['implementation']=='history/nodes/001/artifacts/model.py'
    assert indexed['model']['implementation']=='history/nodes/002/artifacts/model.py'
    assert all(path.read_bytes()==raw for path,raw in documents.items())


def test_inherited_prefix_only_a_resolves_exact_alias_before_local_raw_id_collision(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from scidiscovery.research_loop import accepted_prefix,candidate_refs,instance_display,originals
    parent=tmp_path/'parent';child=tmp_path/'child';parent.mkdir();child.mkdir()
    origin=dict(goal_id='prefix-parent',settings_ref=dict(path=str(parent/'settings.json')))
    prefix=dict(version=1,cutoff=1,count=1,branch=origin,
        visibility_root=dict(path='host-history/accepted-public/prefix-parent'))
    source=SimpleNamespace(original_root=str(parent),origin_identity=origin,step=2)
    job=dict(step=2,role='A',worker_view_version=1,research_strategy_version=3,history=dict(prefix=prefix))
    record=parent/'native-evidence/002';record.mkdir(parents=True)
    payload=dict(summary='Parent assessment',next_action='wait',candidate_recommendation='W001-C001')
    result=dict(payload,candidate_recommendation='model')
    raw=record/'science-output.json';raw.write_text(json.dumps(payload))
    normalized=record/'result.json';normalized.write_text(json.dumps(result))
    original_bytes=(raw.read_bytes(),normalized.read_bytes())
    monkeypatch.setattr(instance_display,'_saved',lambda root,step:(child,source,dict(materials=[]),raw))
    monkeypatch.setattr(originals,'source_job',lambda exact:job)
    monkeypatch.setattr(originals,'source_record',lambda exact,name:record/name)
    looked_up=[]
    def candidate(root,cut,identity):
        assert root==parent and cut==prefix and cut['cutoff']==1
        looked_up.append(identity)
        return dict(origin_identity=origin,observation=dict(id='model'))
    monkeypatch.setattr(accepted_prefix,'candidate',candidate)
    monkeypatch.setattr(accepted_prefix,'current',lambda *args:pytest.fail('UI cannot use a live prefix'))
    assessment=instance_display.node(child,2)
    parent_id=candidate_refs.origin_candidate(origin,'model')
    assert assessment['payload']['candidate_recommendation']==parent_id
    assert assessment['result']['candidate_recommendation']==parent_id
    assert not assessment['candidate_reference_unresolved']
    assert looked_up==['W001-C001','model']
    def observation(identity,step):
        return dict(candidates=[dict(id=identity,label='Candidate '+str(step),implementation=f'history/nodes/{step:03d}/artifacts/model.py',
            evaluation_key='declared',metric=None,direction=None,status='unknown',evidence=[])],
            current_candidate=None,actual_submission=None,coverage=dict(status='partial'))
    earlier=sealed(1,'W',dict(summary='Parent observation'),observation=observation(parent_id,1))
    later=sealed(3,'W',dict(summary='Local observation'),observation=observation('model',3))
    reader=FakeReader({1:earlier,2:assessment,3:later});reader.root=child
    projected=ui_view.candidate_projection(child,4,reader=reader,
        job=dict(step=4,role='A',history=dict(nodes=[dict(step=step) for step in (1,2,3)])))
    assert projected['recommended']==parent_id
    assert {row['id'] for row in projected['index']}=={parent_id,'model'}
    assert (raw.read_bytes(),normalized.read_bytes())==original_bytes
