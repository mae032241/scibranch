"""Native strict output schemas and immutable legacy packets; no model calls."""
import pytest

from scidiscovery.research_loop import contracts as c, protocol
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_history_handoff import finish, scene, source, selection_explanation


def snapshot(paths):
    return {path: (path.lstat().st_mode, path.read_bytes() if path.is_file() else None)
            for path in paths}


def native_strict_schema(schema):
    # Native strict response_format requires this equality, beyond JSON Schema validity.
    if 'properties' in schema:
        assert set(schema['required']) == set(schema['properties'])
        assert schema['additionalProperties'] is False
    # Visit schema-bearing positions, not property names or enum/default data.
    for keyword in ('properties', '$defs', 'definitions'):
        for nested in schema.get(keyword, {}).values():
            native_strict_schema(nested)
    for keyword in ('items', 'additionalProperties'):
        if isinstance(schema.get(keyword), dict):
            native_strict_schema(schema[keyword])
    for keyword in ('anyOf', 'oneOf', 'allOf', 'prefixItems'):
        for nested in schema.get(keyword, []):
            native_strict_schema(nested)


@pytest.mark.parametrize(('role', 'options'), [
    ('H', {}), ('W', {}), ('A', {}), ('E', {}),
    ('A', dict(comparison=True)), ('A', dict(review_enabled=True)),
    ('A', dict(final_only=True)), ('A', dict(final_only=True, final_delivery=True)),
    ('A', dict(formal_review=True)),
])
def test_current_scientific_contract_requires_only_transport_fields(role, options):
    schema=c.result_schema(role, c.HANDOFF_PROTOCOL, **options)
    if options.get('formal_review'):
        native_strict_schema(schema)
    else:
        expected={'summary','scientific_result','limitations'}
        if role in {'H','A'}:expected|={'next_action','route_reason'}
        if role=='W':expected.add('stage_intent')
        assert set(schema['required'])==set(schema['properties'])==expected
        assert schema['additionalProperties'] is True
    if role not in {'H', 'A'}:
        with pytest.raises(protocol.UnsupportedProtocol, match='Scientific result schema'):
            c.result_schema(role, 3)


def test_base_payload_retains_extra_scientific_content_without_nested_schema(tmp_path):
    raw=tmp_path/'science-output.json'
    payload=dict(summary='Bounded result',scientific_result='Evidence remains incomplete',limitations=[],
        next_action='continue_w',route_reason='A contrasting observation is needed',
        selection_explanation={'free_form':'Scientific criteria are authored here'},
        portfolio_review={'extra':[{'unconstrained':'authored science'}]},
        search_review={'dispositions':[],'scientific_note':'Retain alternatives'},
        candidate_nominations='Additional prose may be retained')
    c.write(raw,payload)
    assert c.science_payload(raw,'A')==payload
    assert c.read(raw)==payload
    payload['next_action']='invented_action'
    c.write(raw,payload)
    with pytest.raises(OutputRepairRequired):c.science_payload(raw,'A')


def test_assessment_request_replaces_global_planning_consumers():
    job=dict(role='A',search={},final_coverage_mechanisms=[dict(mechanism_ref='h001.m1')])
    fields=c.response_fields(job,dict(search_policy={}))
    assert 'decision_request' in fields
    assert not {'search_review','portfolio_review','final_coverage'} & set(fields)
    assert not {'portfolio_review','final_coverage'}&set(c.result_schema('A')['properties'])
    assert c.response_fields(dict(role='A'),dict(search_policy=None))==['decision_request']


def notice_context_bytes(job,intent,backend,**context):
    """Count each reachable token/time/progress developer notice once.

    The hook marks each dimension once, regardless of later cause changes.
    Use its longest reachable node/shared/global-exhaustion text for this branch.
    """
    if not (backend.name=='codex' and backend.standard and backend.options is not None):return 0
    from scidiscovery.research_loop import budget_notice
    author,final_return=bool(context.get('author')),bool(context.get('final_return'))
    ordinary=not (author or final_return or job.get('formal_review') or job.get('purpose')=='closeout'
        or job.get('final_report') or job.get('final_only') or intent.get('output_repair') or intent.get('work_repair'))
    roles=('W','A') if job['role']=='H' else ('A',) if job['role']=='W' else ()
    total=0
    for dimension in ('token','time'):
        followups=[None]
        if ordinary and dimension=='token' and roles:followups.append(dict.fromkeys(roles,1))
        notices=(budget_notice.closeout_notice(job,intent,dimension=dimension,
            target_reached=True,shared_reached=shared,shared_exhausted=exhausted,
            shared_expired=dimension=='time' and shared,followup_tokens=followup,
            author=author,final_return=final_return)
            for shared,exhausted in ((False,False),(False,True),(True,True))
            for followup in followups)
        # A branch with no reachable advice contributes no text.
        total+=max((len(notice['text'].encode()) for notice in notices if notice is not None),default=0)
    # Cadence advice follows native context telemetry; an eligible W can reach any of these texts.
    probe=budget_notice.worker_progress_notice(job,intent,active_seconds=1,target_seconds=1,
        fragment_enabled=bool(job.get('worker_fragment_policy')),author=author,final_return=final_return,
        max_context_windows=1,context_observation=dict(context_windows=dict(coverage='unknown')))
    if probe is not None:
        total+=max(len(text.encode()) for text in (budget_notice.WORKER_CONTEXT_TEXT,
            budget_notice.WORKER_CONTEXT_UNKNOWN_TEXT,budget_notice.WORKER_CONTEXT_90_TEXT,
            budget_notice.WORKER_CONTEXT_NEAR_TEXT))
    return total


@pytest.mark.parametrize('scope',['ordinary','wake','repair','author','final','closeout'])
def test_fixed_contract_counts_progress_notice_only_for_eligible_worker(scope):
    from scidiscovery.research_loop import backends,budget_notice
    backend=backends.selected(dict(native_backend='codex',native_launcher_argv=['codex'],native_options={}),
        schema_transport='prompt')
    job=dict(role='W',worker_fragment_policy=dict(version=2,context='same_session'))
    intent={};context={}
    if scope=='wake':intent['execution_wake']={}
    elif scope=='repair':intent['output_repair']=dict(code='fixture')
    elif scope=='author':context['author']=dict(facts='retained')
    elif scope=='final':context['final_return']=True
    elif scope=='closeout':job['purpose']='closeout'
    enabled=notice_context_bytes(job,intent,backend,**context)
    disabled=notice_context_bytes(dict(job,worker_fragment_policy=None),intent,backend,**context)
    longest=max(len(text.encode()) for text in (budget_notice.WORKER_CONTEXT_TEXT,
        budget_notice.WORKER_CONTEXT_UNKNOWN_TEXT,budget_notice.WORKER_CONTEXT_90_TEXT,
        budget_notice.WORKER_CONTEXT_NEAR_TEXT))
    assert enabled-disabled==(longest if scope in {'ordinary','wake'} else 0)


def rendered_contract(tmp_path,job,task,intent,backend,**context):
    """Include actual renderer escaping and the selected backend's request wire."""
    import json
    from scidiscovery.research_loop import native, guidance
    assert job.get('research_strategy_version') == 3
    assert job.get('advisory_scope_version') == 1
    assert isinstance(job.get('native_role_instruction'), str)
    prompt,instruction,projection=native.render_scientific_request(job,task,intent,
        backend_name=backend.name,instruction_transport=backend.instruction_transport,**context)
    schema=tmp_path/'schema.json';c.write(schema,projection)
    packet=backend.request(prompt,schema=schema,profile={},work=tmp_path,response_schema=projection)
    wire=packet if backend.name=='codex' else json.dumps({key:value for key,value in json.loads(packet).items()
        if key in {'prompt','response_schema'}},ensure_ascii=False,separators=(',',':')).encode()
    size=len(wire)+len(json.dumps(task,ensure_ascii=False,indent=2).encode())
    if backend.instruction_transport=='developer':size+=len(instruction.encode())
    if job['role']=='W':size+=len(job['scientific_work_unit'].encode())
    size+=notice_context_bytes(job,intent,backend,**context)
    return size,prompt,instruction,packet


@pytest.mark.parametrize(('role','scope','branch'),[(role,scope,branch)
    for role,scope in [('H','ordinary'),('W','ordinary'),('A','ordinary'),('A','closeout'),('A','final'),('E','ordinary')]
    for branch in ['ordinary','automatic','repair','fragment'] if branch!='fragment' or role=='W'])
@pytest.mark.parametrize('backend_name',['codex','protocol'])
@pytest.mark.parametrize('shared_balance',[0,None],ids=['finite','unlimited'])
def test_complete_fixed_model_response_contract_fits_three_kib(tmp_path,role,scope,backend_name,branch,shared_balance):
    """Measure the production's final renderer and transport, never an AST slice.

    Dynamic goals/original references/budget observations and transport settings
    remain complete and are reported separately. Formal independent review has
    its existing separate contract. Repair includes its actual bounded diagnostic;
    ordinary planning and fragments do not also reserve a repair diagnostic.
    """
    import json
    from scidiscovery.research_loop import backends,guidance
    job=dict(role=role,handoff_protocol=c.HANDOFF_PROTOCOL,research_strategy_version=3,advisory_scope_version=1,search={},
        scientific_response_fields=['portfolio_review','final_coverage'],formal_review_version=1,
        native_role_instruction=guidance.ADVISORY_WORKER_FIRST[role])
    if role=='A':job.update(portfolio_context={},final_coverage_mechanisms=[dict(mechanism_ref='h001.m1')])
    if scope in {'closeout','final'}:job.update(purpose='closeout',search=None)
    if scope=='final':job.update(final_only=True,delivery_sources=dict(items=[{}]))
    if role=='W':job.update(worker_fragment_policy=dict(version=2,context='same_session'),
        scientific_work_unit=max(guidance.WORK_UNITS.values(),key=lambda value:len(value.encode())))
    task=c.response_instructions(job)
    intent={};remaining=None
    diagnosis=OutputRepairRequired('candidate_recommendation_missing','D'*512).request()
    if branch=='automatic':intent['continuation_kind']='automatic_recovery';remaining={}
    elif branch=='repair':intent['work_repair' if role=='W' else 'output_repair']=diagnosis;remaining={}
    elif branch=='fragment':
        intent['continuation_kind']='fragment';remaining={}
    backend=backends.selected(dict(native_backend=backend_name,native_launcher_argv=['codex'] if backend_name=='codex' else ['independent'],
        native_options={} if backend_name=='codex' else None),
        schema_transport='prompt' if backend_name=='codex' else None)
    interface=dict(capabilities={},result_protocol='local-result-v1',executor='local_execution:command') if role=='W' else None
    context=dict(remaining=remaining,final_return=scope=='final',
        execution_interface=interface,managed_command=['experiment'] if role=='W' else None,
        budget_status=dict(shared_seconds_remaining=shared_balance,shared_observed_tokens_remaining=shared_balance))
    size,prompt,instruction,packet=rendered_contract(tmp_path,job,task,intent,backend,**context)
    diagnostic_bytes=0
    if branch=='repair':
        field='work_repair' if role=='W' else 'output_repair'
        empty_intent=dict(intent,**{field:dict(diagnosis,diagnostic='')})
        empty_size,_,_,_=rendered_contract(tmp_path,job,task,empty_intent,backend,**context)
        diagnostic_bytes=size-empty_size
    fixed_size=size-diagnostic_bytes
    import os
    if os.environ.get('SCID_TEST_LOG_DIR'):
        from pathlib import Path
        report=Path(os.environ['SCID_TEST_LOG_DIR'])/'response-contract-sizes.jsonl'
        report.parent.mkdir(parents=True,exist_ok=True)
        with report.open('a') as stream:stream.write(json.dumps(dict(role=role,scope=scope,backend=backend_name,branch=branch,
            shared_balance=shared_balance,
            fixed_bytes=fixed_size,diagnostic_bytes=diagnostic_bytes,rendered_bytes=size,
            diagnostic_allowance_bytes=512 if branch=='repair' else 0,
            cumulative_notice_bytes=notice_context_bytes(job,intent,backend,**context),
            final_prompt_bytes=len(prompt.encode()),instruction_bytes=len(instruction.encode()),transport_packet_bytes=len(packet)))+'\n')
    if branch=='repair':
        assert diagnostic_bytes<=512,(role,scope,backend_name,diagnostic_bytes)
        assert fixed_size+512<=c.MAX_FIXED_RESPONSE_BYTES,(role,scope,backend_name,branch,fixed_size)
    assert size<=c.MAX_FIXED_RESPONSE_BYTES==3072,(role,scope,backend_name,branch,size)


@pytest.mark.parametrize('backend_name',['codex','protocol'])
@pytest.mark.parametrize('text',[
    pytest.param('D'*512,id='ascii512'),
    pytest.param('"'*512,id='quotes'),
    pytest.param('\\'*512,id='backslashes'),
    pytest.param('\n'*512,id='newlines'),
    pytest.param('\x00\x01\t\r'*512,id='controls'),
    pytest.param('界'*512,id='chinese'),
    pytest.param('"\\\n界\x00'*512,id='mixed'),
])
def test_diagnostic_allowance_counts_actual_escaped_renderer_and_backend_wire(tmp_path,backend_name,text):
    import json
    from scidiscovery.research_loop import backends,guidance
    job=dict(role='A',handoff_protocol=c.HANDOFF_PROTOCOL,research_strategy_version=3,advisory_scope_version=1,search={},formal_review_version=1,
        scientific_response_fields=['portfolio_review','final_coverage'],
        final_coverage_mechanisms=[dict(mechanism_ref='h001.m1')],
        native_role_instruction=guidance.ADVISORY_WORKER_FIRST['A'])
    task=c.response_instructions(job)
    backend=backends.selected(dict(native_backend=backend_name,
        native_launcher_argv=['codex'] if backend_name=='codex' else ['independent'],
        native_options={} if backend_name=='codex' else None),
        schema_transport='prompt' if backend_name=='codex' else None)
    diagnosis=OutputRepairRequired('candidate_recommendation_missing',text).request()
    size,_,_,_=rendered_contract(tmp_path,job,task,dict(output_repair=diagnosis),backend,remaining={})
    empty=dict(diagnosis,diagnostic='')
    empty_size,_,_,_=rendered_contract(tmp_path,job,task,dict(output_repair=empty),backend,remaining={})
    def encoded(value):
        result=json.dumps(value,ensure_ascii=False)
        if backend_name=='protocol':result=json.dumps(result,ensure_ascii=False)
        return len(result.encode('utf-8'))
    increment=size-empty_size
    assert increment==encoded(diagnosis['diagnostic'])-encoded('')
    assert increment<=512 and len(diagnosis['diagnostic'].encode('utf-8'))<=512
    assert empty_size+512<=c.MAX_FIXED_RESPONSE_BYTES==3072
    assert size<=c.MAX_FIXED_RESPONSE_BYTES
    if text=='D'*512:assert diagnosis['diagnostic']==text and increment==512
    # Raw UTF-8 truncation alone is insufficient for JSON control characters.
    raw_only=text.encode('utf-8')[:512].decode('utf-8',errors='ignore')
    if backend_name=='protocol' and any(char in raw_only for char in '"\\\n\x00\x01\t\r'):
        unsafe=dict(diagnosis,diagnostic=raw_only)
        unsafe_size,_,_,_=rendered_contract(tmp_path,job,task,dict(output_repair=unsafe),backend,remaining={})
        assert unsafe_size-empty_size>512 and unsafe_size>c.MAX_FIXED_RESPONSE_BYTES


@pytest.mark.parametrize('backend_name',['codex','protocol'])
@pytest.mark.parametrize('scope',['wake','author_closeout','protected_final'])
@pytest.mark.parametrize('shared_balance',[0,None],ids=['finite','unlimited'])
def test_full_renderer_special_suffixes_fit_same_contract(tmp_path,backend_name,scope,shared_balance):
    import json
    from scidiscovery.research_loop import backends,guidance,native
    role='A' if scope=='protected_final' else 'W'
    job=dict(role=role,handoff_protocol=c.HANDOFF_PROTOCOL,research_strategy_version=3,advisory_scope_version=1,native_role_instruction=guidance.ADVISORY_WORKER_FIRST[role])
    if scope=='protected_final':job.update(final_only=True,delivery_sources=dict(items=[{}]),purpose='closeout',
        portfolio_context={},final_coverage_mechanisms=[dict(mechanism_ref='h001.m1')],
        scientific_response_fields=['portfolio_review','final_coverage'])
    else:
        job['managed_execution_wait']={}
        job['worker_fragment_policy']=dict(context='same_session')
        job['scientific_work_unit']=max(guidance.WORK_UNITS.values(),key=lambda value:len(value.encode()))
    intent=dict(execution_wake={}) if 'wake' in scope else {}
    if 'wake' in scope:intent['execution_wake']=dict(original='retained')
    if scope=='author_closeout':intent['continuation_kind']='author_closeout'
    task=c.response_instructions(job)
    backend=backends.selected(dict(native_backend=backend_name,native_launcher_argv=['codex'] if backend_name=='codex' else ['independent'],
        native_options={} if backend_name=='codex' else None),schema_transport='prompt' if backend_name=='codex' else None)
    prompt,instruction,projection=native.render_scientific_request(job,task,intent,backend_name=backend_name,
        instruction_transport=backend.instruction_transport,remaining={},
        author=dict(facts='work/author-closeout-science.json',limits={}) if scope=='author_closeout' else None,
        final_return=scope=='protected_final',
        execution_interface=dict(capabilities={},result_protocol='local-result-v1',executor='local_execution:command') if scope=='wake' else None,
        managed_command=['experiment'] if scope=='wake' else None,
        budget_status=dict(shared_seconds_remaining=shared_balance,shared_observed_tokens_remaining=shared_balance))
    schema=tmp_path/'schema.json';c.write(schema,projection)
    packet=backend.request(prompt,schema=schema,profile={},work=tmp_path,response_schema=projection)
    wire=packet if backend_name=='codex' else json.dumps({key:value for key,value in json.loads(packet).items() if key in {'prompt','response_schema'}},ensure_ascii=False,separators=(',',':')).encode()
    size=len(wire)+len(json.dumps(task,ensure_ascii=False,indent=2).encode())
    if backend.instruction_transport=='developer':size+=len(instruction.encode())
    if role=='W':size+=len(job['scientific_work_unit'].encode())
    size+=notice_context_bytes(job,intent,backend,
        author=dict(facts='work/author-closeout-science.json',limits={}) if scope=='author_closeout' else None,
        final_return=scope=='protected_final')
    if scope=='wake':
        delivered=instruction+'\n'+prompt if backend.instruction_transport=='developer' else prompt
        assert delivered.count(job['native_role_instruction'])==1
        assert instruction.startswith(job['native_role_instruction']+'\n')
    assert size<=c.MAX_FIXED_RESPONSE_BYTES==3072,(backend_name,scope,size)


@pytest.mark.parametrize('choice',[None,'', '   ',[],42])
def test_protected_final_delivery_repairs_missing_explicit_candidate(tmp_path,choice):
    raw=tmp_path/'science-output.json'
    c.write(raw,dict(summary='Final',scientific_result='Retained evidence',limitations=[],
        next_action='deliver',route_reason='Authorized final delivery',candidate_recommendation=choice))
    with pytest.raises(OutputRepairRequired) as error:
        c.science_payload(raw,'A',final_only=True,final_delivery=True)
    assert error.value.code=='candidate_recommendation_missing'


def test_same_task_output_repair_requires_bounded_assessment_request(tmp_path,monkeypatch):
    from scidiscovery.research_loop import continuation,candidate_refs,formal_review,candidate_nominations,search,semantic,delivery_review
    root=tmp_path/'instance';record=c.host_record(c.workspace(root,2))
    job=dict(role='A',step=2,handoff_protocol=c.HANDOFF_PROTOCOL,research_strategy_version=3,advisory_scope_version=1,
        worker_view_version=1,lineage_projection_version=1,evidence_return=delivery_review.make('delivery_review',1,0),
        direct_materials=[],history=dict(nodes=[]),scientific_response_fields=['decision_request'])
    c.write(root/'settings.json',dict(handoff_protocol=c.HANDOFF_PROTOCOL,research_strategy_version=3,advisory_scope_version=1,search_policy=None))
    c.write(root/'settings-identity.json',dict(path='settings.json'))
    c.write(root/'jobs/002.json',job)
    raw=record/'science-output.json'
    science=dict(summary='Finding',scientific_result='Retained evidence',limitations=[],next_action='continue_w',route_reason='More evidence is needed')
    c.write(raw,science)
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,usage=dict(unknown_steps=[]),raw_ref=c.file_ref(raw)))
    inventory=c.CurrentInventory(root,job,record,0,[],{},policy=dict(search_policy=None),
        science_available=True,payload=science,interpretation=dict(focus=None,allocation=None,diagnostics=[]))
    monkeypatch.setattr(continuation,'validate_task',lambda *args,**kwargs:job)
    monkeypatch.setattr(candidate_refs,'control_payload',lambda job,science,**kwargs:science)
    monkeypatch.setattr(formal_review,'validate_choice',lambda *args,**kwargs:None)
    monkeypatch.setattr(semantic,'author_choices',lambda *args,**kwargs:(inventory.interpretation,[]))
    def forbidden(*args,**kwargs):pytest.fail('Retired global inventory cannot gate an assessment request')
    monkeypatch.setattr(search,'require_portfolio_review',forbidden)
    monkeypatch.setattr(candidate_nominations,'require_final_coverage',forbidden)
    request=continuation.output_request(root,2,record,inventory=inventory,source_attempt=0)
    assert request['code']=='a_delivery_review_incomplete'
    assert request['raw_ref']==c.file_ref(raw) and request['source_attempt']==0


def test_accepted_output_repair_keeps_original_diagnostic_but_checks_raw_identity(tmp_path,monkeypatch):
    from scidiscovery.research_loop import continuation
    root=tmp_path/'instance';prior=c.host_record(c.workspace(root,1))
    c.write(root/'jobs/001.json',dict(role='A'))
    raw=prior/'science-output.json';c.write(raw,dict(scientific_result='Retained original'))
    outcome=dict(returncode=0,stop_reason=None,usage=dict(unknown_steps=[]),raw_ref=c.file_ref(raw))
    c.write(prior/'native-outcome.json',outcome)
    request=dict(code='selection_explanation_incomplete',diagnostic='The original accepted diagnostic',
        source_attempt=0,outcome_ref=c.record_ref(prior/'native-outcome.json'),raw_ref=c.file_ref(raw))
    intent=dict(attempt=1,continuation_kind='repair',output_repair=request)
    c.write(prior/'continuations/001/native-intent.json',intent)
    monkeypatch.setattr(continuation.configuration_updates,'for_step',lambda *args:dict(resume_policy={}))
    monkeypatch.setattr(continuation,'validate_launcher',lambda *args:dict(output_repair=True))
    def no_rejudgment(*args,**kwargs):raise AssertionError('Accepted diagnostics must not be recomputed')
    monkeypatch.setattr(continuation,'output_request',no_rejudgment)
    continuation.validate_repair(root,1,intent,prior_record=prior)
    raw.write_text('Changed original bytes')
    with pytest.raises(ValueError,match='raw original changed'):
        continuation.validate_repair(root,1,intent,prior_record=prior)


def test_inherited_original_context_uses_child_storage_not_unavailable_parent(tmp_path):
    from scidiscovery.research_loop import originals
    child=tmp_path/'child';parent=tmp_path/'unavailable-parent'
    identity=dict(goal_id='origin',settings_ref=dict(path=str(parent/'settings.json')))
    source=originals.NodeSource(identity,2,str(parent),child/originals.FORK_DIRECTORY,entries=())
    assert originals.context_root(source)==child
    local=originals.NodeSource(identity,2,str(parent),parent)
    assert originals.context_root(local)==parent
    misplaced=originals.NodeSource(identity,2,str(parent),child/'other-storage',entries=())
    with pytest.raises(ValueError,match='declared fork storage'):
        originals.context_root(misplaced)


def test_optional_progress_and_decision_read_only_consumer_keys():
    job=dict(role='A',scientific_response_fields=['progress_assessment','decision_request'])
    payload=dict(progress_assessment=dict(verdict='progress',explanation='Extra scientific detail'),
        decision_request=dict(decision='candidate',question='Which candidate?',change_criterion='A contrasting finding',extra=True))
    assert c.scientific_field(job,payload,'progress_assessment')==dict(verdict='progress')
    assert c.scientific_field(job,payload,'decision_request')['extra'] is True
    payload['progress_assessment']['verdict']=[]
    payload['decision_request']['question']=None
    assert c.scientific_field(job,payload,'progress_assessment') is None
    assert c.scientific_field(job,payload,'decision_request') is None


@pytest.mark.parametrize('recommendation', [None, 'run-33'])
def test_current_a_scientific_recommendation_can_be_null_or_candidate(tmp_path, recommendation):
    raw = tmp_path / 'science-output.json'
    payload = dict(summary='Independent finding', scientific_result='Bounded result', limitations=[],
                   next_action='wait', route_reason='More evidence needed', candidate_recommendation=recommendation,
                   search_review=None, progress_assessment=None, implementation_selection=None,
                   decision_request=None, portfolio_review=None)
    payload['selection_explanation']=selection_explanation() if recommendation is not None else None
    c.write(raw, payload)
    assert c.science_payload(raw, 'A', c.HANDOFF_PROTOCOL) == payload
    payload.pop('candidate_recommendation')
    c.write(raw, payload)
    assert c.science_payload(raw,'A',c.HANDOFF_PROTOCOL)==payload


@pytest.mark.parametrize('retired', [1, 2, 3])
def test_legacy_a_schema_and_raw_validation_reject_without_rewriting_original(tmp_path, retired):
    raw = tmp_path / 'science-output.json'
    payload = dict(summary='Legacy finding', scientific_result='Original science', limitations=[],
                   next_action='wait', route_reason='Bounded uncertainty')
    c.write(raw, payload)
    originals = (tmp_path, raw)
    before = snapshot(originals)
    with pytest.raises(protocol.UnsupportedProtocol, match='Scientific result schema'):
        c.result_schema('A', retired)
    assert snapshot(originals) == before
    with pytest.raises(protocol.UnsupportedProtocol, match='Scientific result schema'):
        c.science_payload(raw, 'A', retired)
    assert snapshot(originals) == before


def test_current_a_rejects_explicit_wait_without_rewriting_scientific_original(scene):
    source(scene)
    with pytest.raises(OutputRepairRequired):
        finish(scene, 3, 'A', [2], action='wait')
    record=c.host_record(c.workspace(scene,3))
    original=(record/'science-output.json').read_bytes()
    assert c.read(record/'science-output.json')['next_action']=='wait'
    assert not (record/'result-identity.json').exists()
    assert (record/'science-output.json').read_bytes()==original
    assert not (scene/'jobs/004.json').exists()


@pytest.mark.parametrize('retired', [3, 4])
def test_legacy_a_packet_cannot_seal_or_rebuild_without_changing_originals(tmp_path, retired):
    # These are retained archive bytes, never output from a retired write path.
    root = tmp_path / 'archive'
    c.write(root / 'settings.json', dict(handoff_protocol=retired))
    c.write(root / 'settings-identity.json', dict(path='settings.json'))
    c.write(root / 'jobs/003.json', dict(step=3, role='A', handoff_protocol=retired, material_refs=[]))
    work = c.workspace(root, 3);record = c.host_record(work)
    c.write(work / 'science-task.json', dict(preserved_task='Original scientific limitations'))
    c.write(record / 'science-result.schema.json', dict(preserved_schema='Original archived schema'))
    payload = dict(summary='Legacy finding', scientific_result='Original science', limitations=[],
                   next_action='revise_h', route_reason='Bounded uncertainty')
    if retired == 4:
        payload['candidate_recommendation'] = None
    c.write(record / 'science-output.json', payload)
    originals = (root, root / 'settings.json', root / 'settings-identity.json',
                 root / 'jobs', root / 'jobs/003.json', work, work / 'science-task.json',
                 record, record / 'science-result.schema.json', record / 'science-output.json')
    before = snapshot(originals)
    for write,error,diagnostic in (
        (lambda: c.seal_result(root, 3),ValueError,'Accepted budget head is required'),
        (lambda: c.build_job(root, 3, 'A', [2]),protocol.UnsupportedProtocol,'Instance'),
        (lambda: c.build_job(root, 4, 'H', [3]),protocol.UnsupportedProtocol,'Instance')):
        with pytest.raises(error, match=diagnostic):
            write()
        assert snapshot(originals) == before
        assert not (record / 'result-identity.json').exists()
        assert not (root / 'jobs/004.json').exists()
