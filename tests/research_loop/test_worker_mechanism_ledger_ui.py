"""Public owner facts reach the UI ledger without current receipt acquisition."""
from copy import deepcopy
import json

from scidiscovery.research_loop import ui_mechanisms as ledger, ui_saved_status, worker_research
from test_ui_mechanisms import Reader, clean, commitment, h, node
from test_worker_research_admission import batch, config


def public_research(state):
    # Exercise the actual JSON/public schema boundary, not private state rows.
    value=json.loads(json.dumps(worker_research.project(state)))
    public=ui_saved_status._public_research(value)
    assert public is not None
    return public


def owner_view(state, *, step=1, calls=(), coverage='observed'):
    return dict(step=step,research=public_research(state),calls=list(calls),
        coverage=coverage,omitted_count=0)


def test_unsealed_w_declaration_reaches_ledger_without_a_sealed_original(tmp_path):
    selected=config(tmp_path)
    state,_=worker_research.prepare(selected,worker_research.initial(selected),batch())
    view=owner_view(state)
    originals=deepcopy(view)
    reader=Reader({})
    projected=ledger.project(reader,[],clean,live_executions=view)
    rendered=ledger.render(projected)
    assert reader.read==[] and view==originals
    assert 'W 已声明机制' in rendered and '尚未封存' in rendered
    assert 'Which retained input can distinguish this explanation?' in rendered
    assert 'revision 1' in rendered and '活跃' in rendered
    assert '声明来源 W · 第 1 阶段' in rendered and 'href="/progress"' in rendered
    assert '/results/1' not in rendered and '/nodes/1/science' not in rendered
    assert '声明、运行状态与调用数均不表示已验证结论' in rendered
    assert '封存 W 作者发现' in rendered and '本窗口尚未记录逐机制的作者发现' in rendered
    assert '<details class="mechanism-details"' in rendered and ' open' not in rendered


def test_revision_retirement_and_call_binding_keep_exact_identity_and_old_question(tmp_path):
    selected=config(tmp_path)
    state,binding=worker_research.prepare(selected,worker_research.initial(selected),batch())
    old_binding=worker_research.public_mechanism(binding['research_binding'])
    state=worker_research.evolve(state,[dict(action='retire',mechanism_ref='w001.m1')],
        scope=selected['scope'],original_ref=dict(path=str(tmp_path/'retirement.json')))
    following=config(tmp_path,step=3)
    following['worker_research_seed']=list(state['mechanisms'].values())
    state,_=worker_research.prepare(following,worker_research.initial(following),
        batch('w003.m1'))  # Deliberately the same authored label/question, different W identity.
    view=owner_view(state,step=3,calls=[
        dict(call_id='original-call',state='running',display=dict(research_binding=old_binding)),
        dict(call_id='unbound-call',state='unknown',display=dict(mechanism=old_binding['label']))])
    projected=ledger.project(Reader({}),[],clean,live_executions=view)
    first,second=projected['mechanisms']
    assert first['ref']=='w001.m1' and second['ref']=='w003.m1'
    assert first['declaration_status']=='retired' and first['revision']==2
    assert second['declaration_status']=='active' and second['revision']==3
    assert first['events'][-1]['revision']==1
    assert all(event.get('call_id')!='original-call' for event in second['events'])
    rendered=ledger.render(projected)
    assert rendered.count('<article class="mechanism-card">')==2
    assert '已退役' in rendered and '活跃' in rendered and '准确绑定 revision 1' in rendered
    assert 'original-call' in rendered and '已观察运行' in rendered
    assert 'unbound-call' in rendered and '机制关联未知' in rendered and '状态未知' in rendered
    assert '声明来源 W · 第 1 阶段' in rendered and 'href="/results/1"' in rendered
    assert '声明来源 W · 第 3 阶段' in rendered and '/results/3' not in rendered


def test_live_public_results_keep_reported_values_and_exact_call_bindings(tmp_path):
    selected=config(tmp_path)
    state,binding=worker_research.prepare(selected,worker_research.initial(selected),batch())
    original_binding=worker_research.public_mechanism(binding['research_binding'])
    view=owner_view(state,calls=[
        dict(call_id='reported-call',state='known_success',updated_at=100,
            display=dict(research_binding=original_binding,entry='probe <entry>',purpose='Measure the held-out response.',
                metrics=[dict(name='F1 <held-out>',value=.457,unit=''),dict(name='zero',value=0,unit='count'),
                    dict(name='annotation',value='<reported>',unit='<unit>')],metrics_partial=True,result_status='available')),
        dict(call_id='pending-call',state='running',updated_at=200,
            display=dict(research_binding=original_binding,entry='Pending probe',metrics=[],result_status='missing')),
        dict(call_id='older-call',state='known_success',updated_at=50,
            display=dict(research_binding=original_binding,metrics=[dict(name='phase_correlation',value=.9)],
                result_status='available')),
        dict(call_id='unbound-result',state='known_success',updated_at=300,
            display=dict(mechanism=original_binding['label'],metrics=[dict(name='unbound-only',value=999)]))])
    originals=deepcopy(view)
    reader=Reader({})
    projected=ledger.project(reader,[],clean,live_executions=view)
    item,=projected['mechanisms']
    reported,pending,older=[event for event in item['events'] if event.get('kind')=='live_call']
    assert reader.read==[] and view==originals
    assert (reported['call_id'],reported['revision'],reported['source_step'],reported['source_role'])==('reported-call',1,1,'W')
    assert reported['updated_at']==100 and reported['metrics'][1]['value']=='0'
    assert pending['metrics']==[] and pending['result_status']=='missing'
    assert [event['call_id'] for event in projected['live']['unbound_calls']]==['unbound-result']
    for compact in (False,True):
        rendered=ledger.render(projected,compact=compact)
        summary,details=rendered.split('<details class="mechanism-details"',1)
        assert '最近实验结果' in summary and 'reported-call' in summary and 'pending-call' not in summary
        assert 'F1 &lt;held-out&gt; = 0.457' in summary and 'zero = 0 count' in summary
        assert 'annotation = &lt;reported&gt; &lt;unit&gt;' in summary and '仅显示部分指标' in summary
        assert 'probe &lt;entry&gt;' in summary and 'Measure the held-out response.' in summary
        assert '准确绑定 revision 1' in summary and '执行成功' in summary
        assert 'phase_correlation' not in summary and 'phase_correlation = 0.9' in details
        assert '结果指标未报告' in details and '声明结果指标：0' not in details
        assert '执行观察不表示科学认可' in details and 'unbound-only' not in rendered and '999' not in rendered
        assert '<reported>' not in rendered and '<entry>' not in rendered and '<unit>' not in rendered
        reported_detail=details.split('原调用 reported-call',1)[1].split('</li>',1)[0]
        assert 'Measure the held-out response.' in reported_detail and original_binding['question'] not in reported_detail
    # Equal owner timestamps do not establish which reported result was latest.
    older['updated_at']=reported['updated_at']
    rendered=ledger.render(projected)
    assert '最近实验结果' not in rendered and 'phase_correlation = 0.9' in rendered


def test_history_keeps_h_and_w_authors_separate_from_live_declarations(tmp_path):
    sealed_w=node('W',dict(search_plan=dict(commitments=[commitment('Historical W')],
        focus=dict(index=1,action='screen'))),observation=dict(managed_calls=[],candidates=[]))
    selected=config(tmp_path,step=3)
    state,_=worker_research.prepare(selected,worker_research.initial(selected),batch('w003.m1'))
    projected=ledger.project(Reader({1:h(commitment('Historical H')),2:sealed_w}),[1,2],clean,
        live_executions=owner_view(state,step=3))
    assert [(item['ref'],item['source_role']) for item in projected['mechanisms']]==[
        ('h001.m1','H'),('w002.m1','W'),('w003.m1','W')]
    rendered=ledger.render(projected)
    primary,history=rendered.split('<h3>历史机制与明确绑定记录</h3>',1)
    assert 'Historical W' in primary and 'Which retained input' in primary
    assert 'Historical H' not in primary and 'Historical H' in history
    assert '声明来源 H · 第 1 阶段' in history and '/nodes/1/science' in history
    assert '声明来源 W · 第 2 阶段' in primary and '/nodes/2/science' in primary


def test_missing_empty_partial_and_escaped_public_declarations_stay_explicit(tmp_path):
    selected=config(tmp_path)
    empty=worker_research.initial(selected)
    rendered=ledger.render(ledger.project(Reader({}),[],clean,live_executions=owner_view(empty)))
    assert 'W 尚未声明机制' in rendered
    missing=ledger.render(ledger.project(Reader({}),[],clean,
        live_executions=dict(step=1,coverage='unknown',calls=[])))
    assert '机制声明观察不可用' in missing and '缺失观察不表示尚未声明' in missing
    state,_=worker_research.prepare(selected,empty,batch(question='<script>same</script> & uncertain'))
    view=owner_view(state,coverage='partial')
    view['research']['omitted_count']=2
    view['omitted_count']=3
    rendered=ledger.render(ledger.project(Reader({}),[],clean,live_executions=view),compact=True)
    assert '<script>' not in rendered and '&lt;script&gt;' in rendered and '&amp; uncertain' in rendered
    assert '部分观察' in rendered and '另有 2 个声明' in rendered and '另有 3 个原调用' in rendered
    assert '调用仅为已观察下界' in rendered and '完整观察' not in rendered


def test_real_unsealed_first_w_owner_view_reaches_overview_results_and_sse(tmp_path,monkeypatch):
    from scidiscovery.research_loop import contracts as c, controller, experiment_client, experiments, protocol
    from test_instance_ui import session

    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Retain explicit mechanisms and missing observations.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['never-launched'],native_backend='protocol',
        model='ui-fixture',reasoning_effort='high',max_tokens=100000,max_seconds=120,max_steps=6,
        research_strategy_version=3)
    job=c.build_job(root,1,'W',[])
    directory=experiment_client._record_directory_from_facts(root,1,job)
    directory.mkdir(parents=True,exist_ok=True)
    selected=dict(record_dir=str(directory),scope=protocol.stage_scope_for_job(root,job,
        task_ref=c.record_ref(root/'jobs/001.json')),research_strategy_version=3,
        breadth=job['search']['breadth'],worker_research_goal_id=c.settings(root)['goal_id'])
    state,binding=worker_research.prepare(selected,worker_research.initial(selected),batch())
    experiments.Records(selected).research_status(state)
    key=tmp_path/'operator.key';key.write_bytes(b'ui-live-ledger-fixture-secret-32bytes');key.chmod(0o600)
    ui,cookie=session(root,key)
    originals={path:path.read_bytes() for path in (root/'jobs/001.json',directory/'live-status.json')}
    # UI consumers use the receipt owner's public view; they never load its
    # mutable declaration pointer as an alternate source.
    def no_current_reload(*args,**kwargs):
        raise AssertionError('UI consumer reopened current declarations')
    monkeypatch.setattr(worker_research,'load',no_current_reload)
    original_live=ui_saved_status._live_executions
    original_binding=worker_research.public_mechanism(binding['research_binding'])
    supplied_call=dict(call_id='public-result-call',state='known_success',updated_at=100,
        attempt=1,display=dict(research_binding=original_binding,entry='Public probe',
            purpose='Measure the retained response.',metrics=[dict(name='F1',value=.457,unit='')],
            metrics_partial=False,result_status='available'))
    # Inject admitted public owner facts at their producer seam. This exercises
    # shared rendering, not experiment admission or raw result acquisition.
    def public_with_call(root,job):
        value=original_live(root,job)
        return dict(value,calls=[supplied_call])
    monkeypatch.setattr(ui_saved_status,'_live_executions',public_with_call)
    overview=ui.get('/',host=ui.host,cookie=cookie)[1].decode()
    results=ui.get('/results',host=ui.host,cookie=cookie)[1].decode()
    fragments=ui._live_snapshot(ui._browser(cookie),'',force=True)['fragments']
    for markup in (overview,results,fragments['live-usage'],fragments['live-mechanisms']):
        assert 'W 已声明机制' in markup and 'w001.m1' in markup
        assert 'Which retained input can distinguish this explanation?' in markup
        assert 'revision 1' in markup and '尚未封存' in markup
        assert 'href="/progress"' in markup and '声明来源 W · 第 1 阶段' in markup
        assert '探索投入提示' not in markup and str(root) not in markup
        ledger_markup=markup.split('W 已声明机制',1)[1]
        summary=ledger_markup.split('<details class="mechanism-details"',1)[0]
        assert '最近实验结果' in summary and 'F1 = 0.457' in summary
        assert 'public-result-call' in summary and 'Public probe' in summary
        assert '准确绑定 revision 1' in summary and 'Measure the retained response.' in summary
    assert 'id="live-mechanisms"' in results
    assert not (root/'native-evidence/001/result-identity.json').exists()
    assert all(path.read_bytes()==original for path,original in originals.items())
