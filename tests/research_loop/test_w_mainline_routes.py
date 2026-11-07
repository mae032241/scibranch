"""Current W routes reuse serial jobs and exact direct-parent bindings."""
import pytest

from scidiscovery.research_loop import budget_notice, checkpoint, contracts as c, controller, protocol


@pytest.fixture
def routing(tmp_path, monkeypatch):
    record=tmp_path/'record'
    record.mkdir()
    monkeypatch.setattr(controller.execution,'record',lambda *args:record)
    monkeypatch.setattr(controller,'_record',lambda *args:record)
    monkeypatch.setattr(controller.contracts,'read',lambda *args:dict(control_outcome={}))
    return record


@pytest.mark.parametrize(('intent','role','target'),[
    ('handoff','W',None),('revise_h','H',dict(role='W',step=2)),
    ('review','A',dict(role='W',step=2)),('decision','A',None)])
def test_authored_worker_intent_selects_serial_successor(routing,intent,role,target):
    route=controller._route(routing.parent,2,'W',dict(stage_intent=intent),dict(role='W'),1,None)
    assert route[0]==role and route[1]==[1,2] and route[3]==target
    assert route[-2:]==(1,2)


@pytest.mark.parametrize('partial',[False,True])
def test_decision_request_reaches_a_without_incomplete_relabel(routing,monkeypatch,partial):
    delivery=(dict(status='partial',reason='worker_decision',missing=['h001.m2'],blocked=[])
        if partial else dict(status='complete',reason=None,missing=[],blocked=[]))
    monkeypatch.setattr(controller.contracts,'read',lambda *args:dict(control_outcome=dict(worker_delivery=delivery)))
    request='Should the refined candidate be delivered within the original goal limits?'
    route=controller._route(routing.parent,2,'W',dict(stage_intent='decision',route_reason=request),dict(role='W'),1,None)
    assert route[0]=='A' and route[3] is None
    assert request in route[2] and 'inputs/parents/002/conclusion.json' in route[2]
    assert 'assigned work remains incomplete for worker_decision' not in route[2]
    assert ('Missing assigned observations: h001.m2.' in route[2]) is partial
    assert 'at a high level' in route[2] and 'no qualification or execution permission' in route[2]


def test_forced_ending_preserves_request_and_does_not_assert_completion(routing,monkeypatch):
    (routing/'stage-end.json').touch()
    monkeypatch.setattr(checkpoint,'stage_end',lambda *args:dict(reason='worker_resume_unsupported'))
    request='Assess the retained evidence within its stated limits.'
    route=controller._route(routing.parent,2,'W',dict(stage_intent='decision',route_reason=request),dict(role='W'),1,None)
    assert route[0]=='A' and request in route[2]
    assert 'worker_resume_unsupported' in route[2] and 'does not establish scientific completion' in route[2]


@pytest.mark.parametrize('reason',['worker_context_review_due','worker_context_observation_unknown','worker_progress_review_due'])
def test_context_boundary_hands_partial_work_directly_to_new_worker(routing,monkeypatch,reason):
    (routing/'stage-end.json').touch()
    monkeypatch.setattr(checkpoint,'stage_end',lambda *args:dict(reason=reason))
    route=controller._route(routing.parent,2,'W',dict(stage_intent='continue'),dict(role='W'),1,None)
    assert route[0]=='W' and route[1]==[1,2] and route[3] is None
    assert checkpoint.disposition(dict(stage_intent='continue'),review_requested=True)=='handoff'


@pytest.mark.parametrize(('role','intent','advice_action'),[
    ('H','revise_h','wait'),('A','review','finish'),('A','review','formal_review')])
def test_consultation_always_returns_to_exact_requesting_worker(routing,monkeypatch,role,intent,advice_action):
    job=dict(role=role,purpose='worker_consultation',evidence_return=dict(role='W',step=2),
        direct_refs=[dict(path='inputs/parents/002/conclusion.json')])
    monkeypatch.setattr(controller.handoff_state,'read_sealed',lambda *args:dict(role='W',stage_intent=intent))
    route=controller._route(routing.parent,3,role,dict(next_action=advice_action),job,1,2)
    assert route[0]=='W' and route[3] is None
    assert route[1]==([3,2] if role=='H' else [1,2,3])
    assert route[-2:]==((3,2) if role=='H' else (1,2))


def test_consultation_refuses_a_different_authored_request(routing,monkeypatch):
    job=dict(role='A',purpose='worker_consultation',evidence_return=dict(role='W',step=2),
        direct_refs=[dict(path='inputs/parents/002/conclusion.json')])
    monkeypatch.setattr(controller.handoff_state,'read_sealed',lambda *args:dict(role='W',stage_intent='decision'))
    with pytest.raises(ValueError,match='authored route'):
        controller._route(routing.parent,3,'A',{},job,1,2)


def test_current_schema_retains_same_job_continue_and_all_explicit_routes():
    assert protocol.CURRENT==33
    assert c.result_schema('W')['properties']['stage_intent']['enum']==[
        'continue','handoff','revise_h','review','decision']
    assert checkpoint.disposition(dict(stage_intent='continue'))=='continue'
    for intent in ('handoff','revise_h','review','decision'):
        assert checkpoint.disposition(dict(stage_intent=intent))==intent
    for previous in (30,31):
        with pytest.raises(protocol.UnsupportedProtocol):c.result_schema('W',previous)


def test_context_advice_promotes_safe_new_worker_without_scientific_verdict():
    for text in (budget_notice.WORKER_CONTEXT_TEXT,budget_notice.WORKER_CONTEXT_UNKNOWN_TEXT):
        assert 'fresh W' in text and 'independent A' not in text
        assert len(text.encode())<=budget_notice.MAX_NOTICE_BYTES
    assert budget_notice.worker_context_reason(1,dict(context_windows=dict(
        coverage='live',windows_started=1,peak_ratio=.85)))=='worker_context_review_due'
    assert budget_notice.worker_context_reason(1,dict(context_windows=dict(
        coverage='live',windows_started=2,peak_ratio=.1)))=='worker_context_review_due'


def test_consulting_a_has_no_global_response_consumers():
    job=dict(role='A',purpose='worker_consultation',formal_review_version=1)
    assert c.response_fields(job,dict(search_policy={},correction_policy={},decision_contract_version=1))==[]
    assert c.response_options(job)['review_enabled'] is False
    rules=c.response_instructions(job)
    assert 'meaningful judgment' in rules['role_contract']
    assert 'then return to W' in rules['role_contract'] and 'delivery_selection' not in rules


def test_real_serial_jobs_preserve_work_through_handoff_and_evidence_consultation(tmp_path,monkeypatch):
    from scidiscovery.research_loop import execution, formal_review, native
    from test_direct_input_bindings import stopped_seals
    from test_history_handoff import finish, scene
    # Use the fixture's producer directly: only filesystem originals are created.
    root=scene.__wrapped__(tmp_path)
    stopped_seals(monkeypatch)
    calls=[]
    def execute(instance,step):
        job=c.read_job(instance/'jobs'/f'{step:03d}.json')
        calls.append(job['role'])
        parents=[int(ref['path'].split('/')[2]) for ref in job['direct_refs']]
        if step in {3,4,5}:
            assert any(row['path']=='inputs/parents/002/artifacts/candidate.py' for row in job['direct_materials'])
        finish(instance,step,job['role'],parents,
            action='experiment' if step==1 else 'finish',
            stage_intent={2:'handoff',3:'review',5:'decision'}.get(step,'decision'),
            files={'candidate.py':'unfinished candidate\n'} if step==2 else None)
    monkeypatch.setattr(native,'execute_native',execute)
    outcome=controller.run(root)
    assert outcome['status']=='finish' and calls==['H','W','W','A','W','A'],dict(outcome=outcome,calls=calls)
    consultation=c.read(root/'jobs/004.json')
    assert consultation['purpose']=='worker_consultation'
    assert consultation['evidence_return']==dict(role='W',step=3)
    successor=c.read(root/'jobs/005.json')
    assert successor.get('purpose') is None and successor['evidence_return'] is None
    assert [row['path'] for row in successor['direct_refs']]==[
        'inputs/parents/001/conclusion.json','inputs/parents/003/conclusion.json','inputs/parents/004/conclusion.json']
    assert execution.home(root,3)!=execution.home(root,5)
    partial=c.read(root/'native-evidence/002/result-identity.json')
    assert 'formal_review' not in partial and successor.get('execution_authority') is None
    assert native.usage(root)['observed_tokens']==60
    before=(root/'host-history/controller-route.json').read_bytes()
    assert controller.run(root)['status']=='finish'
    assert calls==['H','W','W','A','W','A'] and (root/'host-history/controller-route.json').read_bytes()==before
    # A consultant who shaped this W subject belongs to its author set even
    # without selected implementation bytes; a role change proves no independence.
    monkeypatch.setattr(formal_review,'participants',lambda root,job,seal,**options:
        dict(complete=True,participants=[['fixture',str(job['step'])]]))
    authors=formal_review.authors(root,5)
    assert {tuple(row) for row in authors['participants']} >= {
        ('fixture','2'),('fixture','3'),('fixture','4'),('fixture','5')}


def test_real_h_consultation_retains_exact_request_and_returns_new_worker(tmp_path,monkeypatch):
    from scidiscovery.research_loop import native
    from test_direct_input_bindings import stopped_seals
    from test_history_handoff import finish, scene
    root=scene.__wrapped__(tmp_path)
    stopped_seals(monkeypatch)
    calls=[]
    def execute(instance,step):
        job=c.read(instance/'jobs'/f'{step:03d}.json');calls.append(job['role'])
        parents=[int(ref['path'].split('/')[2]) for ref in job['direct_refs']]
        finish(instance,step,job['role'],parents,
            action='experiment' if step==1 else 'wait' if step==3 else 'finish',
            stage_intent='revise_h' if step==2 else 'decision')
    monkeypatch.setattr(native,'execute_native',execute)
    assert controller.run(root)['status']=='finish'
    assert calls==['H','W','H','W','A']
    consultation=c.read(root/'jobs/003.json')
    assert consultation['purpose']=='worker_consultation' and consultation['evidence_return']==dict(role='W',step=2)
    successor=c.read(root/'jobs/004.json')
    assert successor.get('purpose') is None and [row['path'] for row in successor['direct_refs']]==[
        'inputs/parents/003/conclusion.json','inputs/parents/002/conclusion.json']
    view=c.read(c.workspace(root,3)/'science-task.json')
    assert view['consultation']['requester']=='inputs/parents/002/conclusion.json'
    assert view['consultation']['return_role']=='W'


def test_known_context_boundary_seals_original_continue_before_new_job(tmp_path,monkeypatch):
    from scidiscovery.research_loop import continuation, native
    from test_direct_input_bindings import stopped_seals
    from test_history_handoff import finish, scene
    root=scene.__wrapped__(tmp_path)
    stopped_seals(monkeypatch)
    finish(root,1,'H',[],action='experiment')
    inventories=[]
    # Retain the real stopped inventory before publication, so continuation is
    # tested with actual originals rather than a reconstructed science object.
    with monkeypatch.context() as capture:
        capture.setattr(c,'_seal_result',lambda root,step,*,inventory:inventories.append(inventory))
        job,work,record=finish(root,2,'W',[1],stage_intent='continue')
    inventory=inventories[0]
    inventory.checkpoint=checkpoint._capture(root,job,record,inventory=inventory)
    monkeypatch.setattr(checkpoint,'enabled_for_policy',lambda *args,**kwargs:True)
    monkeypatch.setattr(continuation,'fragment_review',lambda *args:
        dict(requested=True,enforced=True,reason='worker_context_review_due'))
    monkeypatch.setattr(continuation,'fragment_start',lambda *args,**kwargs:
        pytest.fail('Context boundary must seal before another same-job native turn'))
    result=continuation.seal_or_repair(root,2,inventory=inventory)
    assert result['stage_intent']=='continue' and (record/'result-identity.json').is_file()
    assert checkpoint.stage_end(root,job,record)['reason']=='worker_context_review_due'
    route=controller._route(root,2,'W',result,job,1,None)
    assert route[0]=='W'
    successor=c.build_job(root,3,route[0],route[1],instruction=route[2],evidence_return=route[3])
    assert successor['step']==3 and successor.get('purpose') is None
    assert native.usage(root)['observed_tokens']==20


@pytest.mark.parametrize('gate',['semantic','share'])
def test_refused_worker_successor_stays_at_exact_seal_without_corrective_role(tmp_path,monkeypatch,gate):
    from scidiscovery.research_loop import native, role_share, semantic
    from test_direct_input_bindings import stopped_seals
    from test_history_handoff import finish, scene
    root=scene.__wrapped__(tmp_path)
    stopped_seals(monkeypatch)
    calls=[]
    def execute(instance,step):
        job=c.read(instance/'jobs'/f'{step:03d}.json');calls.append(job['role'])
        finish(instance,step,job['role'],[int(row['path'].split('/')[2]) for row in job['direct_refs']],
            action='experiment',stage_intent='handoff')
    monkeypatch.setattr(native,'execute_native',execute)
    if gate=='semantic':
        ordinary=semantic.effective_route
        def refuse(root,step,result,job,route):
            if job['role']=='W':raise semantic.ScientificAdmissionRefused('declared_role_cap',next_role='W')
            return ordinary(root,step,result,job,route)
        monkeypatch.setattr(semantic,'effective_route',refuse)
    else:
        ordinary=role_share.effective_route
        def refuse(root,step,result,job,route,**options):
            if job['role']=='W':return (*route,'declared_role_cap')
            return ordinary(root,step,result,job,route,**options)
        monkeypatch.setattr(role_share,'effective_route',refuse)
    for _ in range(2):
        result=controller.run(root)
        assert result['status']=='role_admission_refused' and result['reason']=='declared_role_cap'
        assert result['next_role']=='W' and result['last_step']==2
        assert calls==['H','W'] and not (root/'jobs/003.json').exists()
        assert c.read(root/'host-history/controller-route.json')['completed']==1
