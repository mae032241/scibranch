"""Independent subprocess protocol, never a provider or a Codex-shaped stub."""
import json
import sys

import pytest

from scidiscovery.research_loop import accepted_budget, backends, budget, contracts as c, controller, execution, native


SCRIPT = r'''import json,sys,pathlib
assert len(sys.argv)==2 and sys.argv[1]=='operator-argument',sys.argv
request=json.load(sys.stdin)
assert request['protocol']=='scidiscovery-agent-v1'
assert request['permissions']['enforcement']=='hint_only'
assert 'reasoning_effort' not in request['profile']
assert 'service_tier' not in request['profile']
work=pathlib.Path(request['workspace'])
(work/'work/request.json').write_text(json.dumps(request))
print(json.dumps(dict(type='session',session_id='independent-session')),flush=True)
print(json.dumps(dict(type='usage',usage=dict(input_tokens=12,output_tokens=3))),flush=True)
properties=request['response_schema']['properties']
result={key:None for key in properties}
result.update(summary='Completed bounded work',scientific_result='No scientific success asserted',limitations=['Fixture'],next_action='finish' if properties.get('next_action',{}).get('enum')==['finish'] else 'wait',route_reason='Retain exact original')
print(json.dumps(dict(type='result',result=result)),flush=True)
if not (work/'work/no-terminal').exists():
 print(json.dumps(dict(type='completed',usage=dict(input_tokens=12,output_tokens=3))),flush=True)
'''


def prepare(tmp_path, *, max_steps=3, **options):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Keep a bounded scientific result.')
    program=tmp_path/'independent.py';program.write_text(SCRIPT)
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable,str(program),'operator-argument'],
        native_backend='protocol',native_options={},model='independent-model',reasoning_effort=None,service_tier=None,
        max_tokens=1000,max_seconds=60,max_steps=max_steps,search_policy=None,**options)
    return root


def intent(root, role='H'):
    job=c.build_job(root,1,role,[])
    anchor=budget.accept_node(root,job)
    value=dict(task_ref=c.record_ref(root/'jobs/001.json'),**native.profiles.binding(c.settings(root),job))
    if anchor is not None:value['budget_slice']=anchor
    accepted_budget.accept_native(root,1,execution.origin(root,1),value)
    return execution.origin(root,1)


@pytest.mark.process_e2e
def test_protocol_runs_exact_argv_and_seals_independent_result_and_usage(tmp_path):
    root=prepare(tmp_path);record=intent(root)
    outcome=native.run_native(root,1)
    assert outcome['native_backend']=='protocol'
    assert outcome['native_identity']['session_id']=='independent-session'
    assert outcome['native_identity']['participant_scope']=='unsupported'
    assert c.read(record/'science-output.json')['summary']=='Completed bounded work'
    assert c.read(record/'usage.json')==dict(input_tokens=12,cached_input_tokens=0,output_tokens=3)
    assert outcome['metering']['coverage']=='terminal'
    assert not (root/'native-homes/001/sessions').exists()
    public=[json.loads(line) for line in (record/'native-public.jsonl').read_text().splitlines()]
    assert public and all('session_id' not in row for row in public)


@pytest.mark.process_e2e
def test_protocol_missing_terminal_retains_original_without_verifying_success(tmp_path):
    root=prepare(tmp_path);record=intent(root)
    (c.workspace(root,1)/'work/no-terminal').touch()
    with pytest.raises(RuntimeError,match='preserved without replay'):native.run_native(root,1)
    assert (record/'result.pending.json').is_file()
    assert not (record/'science-output.json').exists()
    assert c.read(record/'native-outcome.json')['metering']['coverage']=='unknown'


@pytest.mark.process_e2e
def test_protocol_can_deliver_result_after_completed_declaration(tmp_path):
    root=prepare(tmp_path);record=intent(root)
    result="print(json.dumps(dict(type='result',result=result)),flush=True)"
    assert SCRIPT.count(result)==1
    (tmp_path/'independent.py').write_text(SCRIPT.replace(result,'')+'\n'+result+'\n')
    outcome=native.run_native(root,1)
    assert outcome['returncode']==0 and outcome['stop_reason'] is None
    assert outcome['metering']['coverage']=='terminal'
    assert c.read(record/'science-output.json')['summary']=='Completed bounded work'
    events=[json.loads(line)['type'] for line in (record/'native.jsonl').read_text().splitlines()]
    assert events[-2:]==['completed','result']


@pytest.mark.process_e2e
@pytest.mark.parametrize('event,reason',[
    (dict(type='completed',usage=dict(input_tokens=12,output_tokens=3)),'native_completion_duplicated'),
    (dict(type='session',session_id='independent-session'),'native_activity_after_completion'),
    (dict(type='message',text='Unfinished later activity'),'native_activity_after_completion'),
    (dict(type='tool',tool='later-execution',status='started'),'native_activity_after_completion'),
    (dict(type='failed'),'native_failure_event'),
    (dict(type='error',message='Unresolved failure'),'native_failure_event'),
])
def test_protocol_rejects_activity_or_failure_after_completion(tmp_path,event,reason):
    root=prepare(tmp_path);record=intent(root)
    (tmp_path/'independent.py').write_text(SCRIPT+'\nprint('+repr(json.dumps(event))+',flush=True)\n')
    with pytest.raises(RuntimeError,match='preserved without replay'):native.run_native(root,1)
    outcome=c.read(record/'native-outcome.json')
    assert outcome['returncode']==0 and outcome['stop_reason']==reason
    assert outcome['metering']['coverage']=='unknown' and outcome['metering']['reason']==reason
    assert c.read(record/'usage.json')==dict(input_tokens=12,cached_input_tokens=0,output_tokens=3)
    assert native.usage(root)['unknown_steps']==[1]
    assert (record/'result.pending.json').is_file()
    assert not (record/'science-output.json').exists()
    assert not (record/'result-identity.json').exists()


def test_protocol_resume_is_explicitly_unsupported(tmp_path):
    root=prepare(tmp_path)
    from scidiscovery.research_loop import continuation
    with pytest.raises(continuation.ResumeUnavailable,match='unsupported_backend'):
        continuation.validate_launcher(root)


def test_optional_role_profile_roundtrip_and_job_binding(tmp_path):
    from scidiscovery.research_loop import profiles
    defaults=dict(model='independent-model',reasoning_effort=None,service_tier=None)
    frozen=profiles.normalize({'H':{'model':'independent-model'}},defaults)
    assert profiles.normalize(frozen,defaults)==frozen
    root=prepare(tmp_path,role_profiles={'H':{'model':'independent-model'}})
    record=intent(root)
    assert c.read(root/'jobs/001.json')['native_profile']==defaults
    assert c.read(record/'native-intent.json')['native_profile']==defaults


@pytest.mark.parametrize('backend,source,expected',[
    ('protocol','protocol.completed',True),('protocol','turn.completed',False),
    ('codex','protocol.completed',False),('protocol','invented.completed',False)])
def test_completion_source_must_match_frozen_backend_and_process_facts(backend,source,expected):
    settings=backends.configuration(backend,['codex'] if backend=='codex' else ['independent'],{})
    outcome=dict(native_backend='protocol',returncode=0,stop_reason=None,
                 cleanup='observed_group_cleaned',metering=dict(source=source,coverage='unknown'))
    assert budget.native_completion_known(outcome,settings) is expected
    for change in (dict(returncode=True),dict(returncode=2),dict(stop_reason='native_error_unknown'),dict(cleanup='cleanup_unknown')):
        assert budget.native_completion_known(dict(outcome,**change),settings) is False
    assert budget.native_completion_known(outcome) is False


def test_protocol_unknown_usage_is_unknown_not_a_zero_observation():
    meter=backends.EventUsage()
    assert meter.poll()['coverage']=='unknown'
    assert meter.poll()['has_observation'] is False
    meter.observe(dict(input_tokens=20,cached_input_tokens=2,output_tokens=1))
    meter.observe(dict(input_tokens=10,cached_input_tokens=2,output_tokens=1))
    assert meter.poll()['coverage']=='unknown'
    assert meter.poll()['tokens']['input_tokens']==20


def test_codex_inline_options_override_one_run_without_changing_auth_home(tmp_path):
    settings=backends.configuration('codex',['codex'],dict(workspace='workspace-write',network=True,web='live',subagents=True))
    backend=backends.selected(settings)
    args=backend.launch(dict(model='chosen',reasoning_effort=None,service_tier=None),tmp_path,tmp_path/'out',tmp_path/'schema','instruction')
    configs=[args[n+1] for n,value in enumerate(args) if value=='-c']
    assert '--ignore-user-config' in args
    assert 'features.multi_agent=true' in configs and 'web_search="live"' in configs
    assert not any(value.startswith(('model_reasoning_effort=','service_tier=')) for value in configs)
    assert backend.complete_authorship is False
    assert not (tmp_path/'config.toml').exists()


@pytest.mark.parametrize('launcher,supported,transport',[
    (['codex'],True,'native_flag'),
    ([sys.executable,'unknown-wrapper.py'],False,'scientific_task')])
def test_codex_native_schema_flag_requires_qualified_launcher_capability(tmp_path,launcher,supported,transport):
    settings=backends.configuration('codex',launcher,{})
    backend=backends.selected(settings)
    assert backend.supports_native_schema_flag is supported
    assert settings['native_capabilities']['response_schema_transport']==transport
    profile=dict(model='chosen')
    args=backend.launch(profile,tmp_path,tmp_path/'out',tmp_path/'schema','instruction')
    resumed,_=backend.resume(args,profile,tmp_path,tmp_path/'out',tmp_path/'schema',dict(thread_id='original'))
    for invocation in (args,resumed):
        assert ('--output-schema' in invocation) is supported
        assert invocation[invocation.index('--output-last-message')+1]==str(tmp_path/'out')
    independent=backends.configuration('protocol',['independent'],{})
    assert independent['native_capabilities']['response_schema_transport']=='protocol_request'


@pytest.mark.parametrize('role',['H','W','A','E'])
def test_codex_prompt_json_transport_keeps_scientific_content_open_and_native_configuration(tmp_path,role):
    settings=backends.configuration('codex',['codex'],dict(
        workspace='workspace-write',network=True,web='cached',subagents=False,
        sandbox_workspace_write=dict(writable_roots=[str(tmp_path/'extra')],network_access=True)))
    profile=dict(model='chosen',reasoning_effort='high',service_tier='fast')
    original_settings=json.loads(json.dumps(settings))
    original_profile=dict(profile)
    schema=tmp_path/'schema.json'
    c.write(schema,c.result_schema(role,c.HANDOFF_PROTOCOL))
    original=schema.read_text()
    prompt='Complete the original scientific task with its retained limitations.'
    native_backend=backends.selected(settings)
    fallback=backends.selected(settings,schema_transport='prompt')
    assert native_backend.active_schema_transport=='native_flag'
    assert fallback.active_schema_transport=='prompt'
    assert fallback.schema_transport==settings['native_capabilities']['response_schema_transport']=='native_flag'
    args=native_backend.launch(profile,tmp_path,tmp_path/'out',schema,'independent rules')
    fallback_args=fallback.launch(profile,tmp_path,tmp_path/'out',schema,'independent rules')
    schema_position=args.index('--output-schema')
    assert fallback_args==args[:schema_position]+args[schema_position+2:]
    request=fallback.request(prompt,profile=profile,work=tmp_path,schema=schema).decode()
    assert native_backend.request(prompt,profile=profile,work=tmp_path,schema=schema).decode()==request
    assert request.startswith(prompt+'\n') and original not in request
    assert request.count('Return only one JSON object as your final response')==1
    assert 'no Markdown fences or surrounding prose' in request
    assert request.endswith(backends.JSON_RESPONSE_INSTRUCTION)
    assert schema.read_text()==original

    # A previously accepted prompt-mode lineage must also resume without the
    # rejected flag, retaining its native model, permission and instruction argv.
    session=dict(thread_id='original')
    native_resume,_=native_backend.resume(args,profile,tmp_path,tmp_path/'out',schema,session)
    fallback_resume,_=fallback.resume(args,profile,tmp_path,tmp_path/'out',schema,session)
    schema_position=native_resume.index('--output-schema')
    assert fallback_resume==native_resume[:schema_position]+native_resume[schema_position+2:]
    assert '--output-schema' not in fallback_resume
    for adapter in (native_backend,fallback):
        assert adapter.request(prompt,profile=profile,work=tmp_path,schema=schema).decode()==request
        repair=adapter.request('Repair the final scientific response.',profile=profile,work=tmp_path,schema=schema).decode()
        assert original not in repair
        assert repair.partition('\n')[2]==request.partition('\n')[2]
    assert schema.read_text()==original
    assert settings==original_settings and profile==original_profile


@pytest.mark.parametrize('transport',['native_flag','prompt'])
def test_strict_schema_shape_never_makes_an_ordinary_request_formal(tmp_path,transport):
    settings=backends.configuration('codex',['codex'],{})
    backend=backends.selected(settings,schema_transport=transport)
    schema=tmp_path/'retained-strict.schema.json'
    c.write(schema,c.result_schema('A',formal_review=True))
    original=schema.read_text()
    ordinary=backend.request('Retain the original task.',schema=schema,formal_review=False).decode()
    assert original not in ordinary
    assert ordinary.endswith(backends.JSON_RESPONSE_INSTRUCTION)
    assert schema.read_text()==original
    independent=backend.request('Assess the exact subject independently.',schema=schema,formal_review=True).decode()
    assert independent.count(original)==(1 if transport=='prompt' else 0)
    assert schema.read_text()==original


def test_ordinary_protocol_projects_current_schema_over_retained_strict_original(tmp_path):
    from scidiscovery.research_loop import native
    job=dict(role='A',handoff_protocol=c.HANDOFF_PROTOCOL,native_role_instruction='Exact retained scientific scope',
        scientific_response_fields=['portfolio_review','final_coverage'],search={},portfolio_context={},
        final_coverage_mechanisms=[dict(mechanism_ref='h001.m1')])
    task=dict(final_response=['summary','legacy_large_required_table'])
    original=dict(type='object',properties=dict(legacy_large_required_table=dict(type='object')),
        required=['legacy_large_required_table'],additionalProperties=False)
    schema=tmp_path/'frozen-schema.json';c.write(schema,original);before=schema.read_bytes()
    backend=backends.selected(backends.configuration('protocol',['independent'],{}))
    prompt,instruction,current=native.render_scientific_request(job,task,{},backend_name='protocol',instruction_transport='prompt')
    packet=json.loads(backend.request(prompt,profile={},work=tmp_path,schema=schema,response_schema=current))
    assert packet['response_schema']==c.result_schema('A',**c.response_options(job))
    assert 'legacy_large_required_table' not in packet['prompt']
    assert 'Current response rules; scope binds:' in packet['prompt']
    assert instruction.startswith(job['native_role_instruction']+'\n')
    assert schema.read_bytes()==before and task['final_response']==['summary','legacy_large_required_table']


@pytest.mark.parametrize('backend,launcher',[
    ('codex',[sys.executable,'operator-wrapper.py']),('protocol',['independent'])])
def test_custom_transports_do_not_gain_implicit_schema_fallback(tmp_path,backend,launcher):
    settings=backends.configuration(backend,launcher,{})
    adapter=backends.selected(settings)
    assert adapter.active_schema_transport==adapter.schema_transport
    with pytest.raises(ValueError,match='response schema transport'):
        backends.selected(settings,schema_transport='prompt')
    schema=tmp_path/'schema.json';c.write(schema,c.result_schema('A',c.HANDOFF_PROTOCOL))
    prompt='Keep this exact operator prompt.'
    request=adapter.request(prompt,profile=dict(model='chosen'),work=tmp_path,schema=schema,response_schema=c.result_schema('A'))
    if backend=='codex':
        assert request==prompt.encode()
    else:
        packet=json.loads(request)
        assert packet['prompt']==prompt
        assert packet['response_schema']==c.read(schema)


@pytest.mark.parametrize('mode,requested,effective,enforcement',[
    ('workspace-write',False,False,'native_sandbox'),
    ('workspace-write',True,True,'native_sandbox'),
    ('danger-full-access',False,True,'unsupported'),
    ('read-only',True,False,'native_sandbox')])
def test_permission_summary_keeps_requested_and_effective_network_distinct(mode,requested,effective,enforcement,tmp_path):
    settings=backends.configuration('codex',['codex'],dict(workspace=mode,network=requested))
    proof=settings['native_permissions']
    assert proof['requested']['network'] is requested
    assert proof['effective']['network'] is effective
    assert proof['enforcement']['network']==enforcement
    backend=backends.selected(settings)
    args=backend.launch(dict(model='chosen'),tmp_path,tmp_path/'out',tmp_path/'schema','instruction')
    assert args[args.index('--sandbox')+1]==mode
    if requested!=effective:assert proof['limitations']


def test_protocol_permission_declaration_does_not_claim_native_isolation():
    settings=backends.configuration('protocol',['independent'],dict(network=False,workspace='read-only'))
    proof=settings['native_permissions']
    assert proof['requested']['network'] is False
    assert proof['effective']['network']=='unknown'
    assert proof['enforcement']['network']=='hint_only'


def test_public_parser_retains_reasoning_summary_and_excludes_raw_tool_details():
    backend=backends.selected(dict(native_backend='codex',native_launcher_argv=['codex']))
    assert backend.public_events(dict(type='item.completed',item=dict(type='reasoning',text='Public native summary')))==[dict(type='thinking',text='Public native summary')]
    assert backend.public_events(dict(type='item.started',item=dict(type='reasoning',text='Unfinished summary')))==[]
    value=backend.public_events(dict(type='item.completed',item=dict(type='command_execution',command='secret',aggregated_output='private')))
    assert value==[dict(type='tool',tool='command_execution',status='completed')]


def test_public_final_preview_keeps_truncation_visible_without_becoming_terminal():
    from scidiscovery.research_loop import public_output
    backend=backends.selected(dict(native_backend='codex',native_launcher_argv=['codex']))
    event=dict(type='item.completed',item=dict(type='agent_message',text='final preview',text_truncated=True))
    projected=public_output.event(backend.public_events(event)[0],lambda text:text)
    assert projected['text_truncated'] is True and '显示片段' in projected['text']
    assert backend.terminal_message(event) is None and backend.terminal_binding(event) is None


@pytest.mark.parametrize('settings',[{},dict(native_backend='unregistered-draft-provider')])
def test_public_error_cleaner_accepts_unprepared_backend_settings(tmp_path,settings):
    from scidiscovery.research_loop import public_output
    clean=public_output.cleaner(tmp_path,settings)
    assert 'private-value' not in clean('api_key="private-value"')
    assert clean(str(tmp_path))=='[private]'


@pytest.mark.parametrize('unknown_usage',[False,True])
@pytest.mark.process_e2e
def test_protocol_final_a_uses_original_protected_completion(tmp_path,monkeypatch,unknown_usage):
    from test_final_a_completion import instance
    script=SCRIPT
    if unknown_usage:
        script=script.replace("print(json.dumps(dict(type='usage',usage=dict(input_tokens=12,output_tokens=3))),flush=True)",'')
        script=script.replace("dict(type='completed',usage=dict(input_tokens=12,output_tokens=3))","dict(type='completed')")
    program=tmp_path/'independent.py';program.write_text(script)
    original=controller.prepare
    def selected(*args,**kwargs):
        args=(*args[:3],[sys.executable,str(program),'operator-argument'])
        return original(*args,**dict(kwargs,native_backend='protocol',native_options={},reasoning_effort=None,service_tier=None))
    monkeypatch.setattr(controller,'prepare',selected)
    root=instance(tmp_path,final=True,max_tokens=1000)
    result=controller.run(root)
    assert result['status']=='final_a_completed'
    assert c.read(execution.origin(root,2)/'science-output.json')['summary']=='Completed bounded work'
    if unknown_usage:
        assert not (execution.origin(root,2)/'usage.json').exists()
        assert c.read(execution.origin(root,2)/'native-outcome.json')['metering']['coverage']=='unknown'


CAPTURE_LAUNCHER = r"""#!/usr/bin/env python3
import json,os,pathlib,sys
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
wire=sys.stdin.read()
args=sys.argv[1:]
protocol=not args
request=json.loads(wire) if protocol else None
prompt=request['prompt'] if protocol else wire
configs=[args[i+1] for i,value in enumerate(args) if value=='-c']
developer=[json.loads(value.split('=',1)[1]) for value in configs if value.startswith('developer_instructions=')]
(work/'work/captured.json').write_text(json.dumps(dict(prompt=prompt,developer=developer,args=args,
 response_schema=request['response_schema'] if protocol else None)))
task=json.loads((work/'science-task.json').read_text())
properties=request['response_schema']['properties'] if protocol else ['summary','scientific_result','limitations']
if not protocol and task['role'] in {'H','A'}:properties+=['next_action','route_reason']
if not protocol and task['role']=='W':properties+=['stage_intent']
result={key:None for key in properties}
result.update(summary='Transport capture',scientific_result='No scientific qualification',limitations=['Local fake transport'],next_action='wait',route_reason='Bounded fixture')
if protocol:
 print(json.dumps(dict(type='result',result=result)),flush=True)
 print(json.dumps(dict(type='completed',usage=dict(input_tokens=3,output_tokens=2))),flush=True)
else:
 pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(result))
 print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=3,output_tokens=2))),flush=True)
"""


@pytest.mark.process_e2e
@pytest.mark.parametrize('transport,role', [('options','A'), ('protocol','A')])
def test_role_rules_have_one_actual_launch_transport_and_task_data_remains(tmp_path, monkeypatch, transport, role):
    import os
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('Retain exact evidence and limitations.')
    program=tmp_path/('codex' if transport=='standard' else 'launcher.py')
    program.write_text(CAPTURE_LAUNCHER);program.chmod(0o700)
    monkeypatch.setenv('PATH',str(tmp_path)+os.pathsep+os.environ['PATH'])
    monkeypatch.setenv('CODEX_HOME',str(tmp_path/'native-config'))
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['codex'] if transport=='standard' else [sys.executable,str(program)],
        native_backend='protocol' if transport=='protocol' else 'codex',
        native_options={} if transport in {'options','protocol'} else None,
        model='fixture',reasoning_effort='high',max_tokens=1000,max_seconds=60,max_steps=4,search_policy=None,experiment_mode='external')
    record=intent(root, role)
    retained_schema=None
    if transport=='standard':
        # A retained ordinary strict schema remains an original input, never
        # an implicit request to send a large legacy contract to the model.
        retained=c.read(record/'science-result.schema.json')
        retained['additionalProperties']=False
        c.write(record/'science-result.schema.json',retained)
        retained_schema=(record/'science-result.schema.json').read_bytes()
    native.run_native(root,1)
    work=c.workspace(root,1);captured=c.read(work/'work/captured.json');task=c.read(work/'science-task.json')
    job=c.read(root/'jobs/001.json')
    rules=job.get('native_role_instruction',c.ROLE_INSTRUCTIONS[role])
    assert sum(text.count(rules) for text in [captured['prompt'],*captured['developer']])==1
    if transport=='standard':
        assert '--output-schema' not in captured['args']
        assert c.read(record/'native-outcome.json')['native_schema_transport']=='prompt'
        assert (record/'science-result.schema.json').read_bytes()==retained_schema
        assert retained_schema.decode() not in captured['prompt']
    if transport in {'standard','options'}:
        _,actual_instruction,_=native.render_scientific_request(job,task,{},backend_name='codex',instruction_transport='developer')
        assert captured['developer']==[actual_instruction] and rules not in captured['prompt']
        assert 'approval_policy="never"' in captured['args']
    else:
        assert not captured['developer'] and rules in captured['prompt']
    assert all(key not in task for key in ('role_instruction','role_guidance','scientific_judgment'))
    assert task['role']==role and task['global']['objective']=='inputs/global/objective.md'
    assert 'final_response' not in task and 'schema' not in task
    current=c.result_schema(role,**c.response_options(job))
    if transport=='protocol':
        assert captured['response_schema']==current
    else:
        required_menu=captured['developer'][0].split('Required: ',1)[1]
        assert all(name in required_menu for name in current['required'])


def test_codex_resume_preserves_the_single_developer_instruction_and_permissions(tmp_path):
    settings=backends.configuration('codex',['custom-launcher'],
        dict(workspace='workspace-write',network=True,web='cached',subagents=True))
    backend=backends.selected(settings)
    args=backend.launch(dict(model='fixture'),tmp_path,tmp_path/'out',tmp_path/'schema','exact independent rules')
    resumed,_=backend.resume(args,dict(model='fixture'),tmp_path,tmp_path/'out',tmp_path/'schema',dict(thread_id='original-thread'))
    configurations=lambda values:[values[i+1] for i,value in enumerate(values) if value=='-c']
    assert configurations(resumed)==configurations(args)
    assert configurations(resumed).count('developer_instructions="exact independent rules"')==1
    assert '--ignore-user-config' in resumed and '--skip-git-repo-check' in resumed
    assert resumed[resumed.index('--sandbox')+1]=='workspace-write'


def test_formal_review_task_retains_subject_and_uses_launcher_for_independent_rules(tmp_path, monkeypatch):
    from scidiscovery.research_loop import formal_review, user_guidance
    public=dict(role='A',question='Assess exact claims',review=dict(subject='inputs/subject',instruction='Independent exact-subject assessment',scope='No execution permission'),final_response=['verdict','findings'])
    monkeypatch.setattr(formal_review,'public',lambda *args:public)
    # This test owns a synthetic empty review packet, not review qualification.
    monkeypatch.setattr(formal_review,'input_binding',lambda *args,**kwargs:dict(materials=[]))
    monkeypatch.setattr(user_guidance,'public',lambda *args:{})
    run=tmp_path/'run';run.mkdir()
    root=prepare(run)
    record=intent(root, 'A')
    job=c.read(root/'jobs/001.json');job['formal_review']={'exact':'original'}
    c.write(root/'jobs/001.json',job)
    value=c.read(record/'native-intent.json');value['task_ref']=c.record_ref(root/'jobs/001.json')
    c.write(record/'native-intent.json',value)
    # Use the same current build_job fixture for projection and launch, including
    # its frozen worker/lineage view versions and complete scientific packet.
    task=c.science_view(job,root)
    assert task['review']==dict(subject='inputs/subject',scope='No execution permission')
    assert public['review']['instruction']=='Independent exact-subject assessment'
    class LaunchCaptured(RuntimeError):
        pass
    def launch(self, profile, work, pending, schema, instruction):
        assert instruction==public['review']['instruction']
        assert c.AUTHOR_EVIDENCE_GUIDANCE not in instruction
        raise LaunchCaptured()
    monkeypatch.setattr(backends.Protocol,'launch',launch)
    with pytest.raises(LaunchCaptured):
        native.run_native(root,1)
