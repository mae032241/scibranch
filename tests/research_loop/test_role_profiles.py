"""Frozen heterogeneous native configuration with local stand-ins; no model calls."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, continuation, execution, native, profiles
from scidiscovery.research_loop.cli import main
import test_worker_resume as resume


OVERRIDES = {'H': {'model': 'gpt-6.1-sol'}, 'W': {'model': 'gpt-6.1-sol'},
             'A': {'model': 'gpt-5.6-sol'}}
DEFAULTS = dict(model='gpt-6.1-sol', reasoning_effort='high', service_tier='default')


def prepare(tmp_path, **changes):
    public = tmp_path / 'public';public.mkdir()
    goal = public / 'goal.md';goal.write_text('Compare scientific alternatives.')
    options = dict(max_tokens=1000, max_seconds=120, max_steps=8, search_policy=None, **DEFAULTS)
    options.update(changes)
    root = tmp_path / 'instance'
    controller.prepare(root, goal, public, ['never-launched'], **options)
    return root


def test_cli_freezes_expanded_roles_and_does_not_expose_host_profile_to_science(tmp_path):
    public=tmp_path/'public';public.mkdir();goal=public/'goal';goal.write_text('Original goal')
    config=tmp_path/'profiles.json';config.write_text(json.dumps(OVERRIDES))
    root=tmp_path/'instance'
    assert main(['prepare','--root',str(root),'--objective',str(goal),'--public-root',str(public),
        '--native-launcher-json','["never-launched"]','--max-tokens','1000','--max-seconds','120',
        '--max-steps','8','--role-profiles',str(config)])==0
    config.write_text('{}')
    settings=c.settings(root)
    assert settings['role_profiles']==profiles.normalize(OVERRIDES,DEFAULTS)
    assert settings['role_profiles']['E']==DEFAULTS
    job=c.build_job(root,1,'H',[])
    assert job['native_profile']==DEFAULTS
    assert 'native_profile' not in c.read(c.workspace(root,1)/'science-task.json')


@pytest.mark.process_e2e
@pytest.mark.parametrize('text',['null','[]','{}'])
def test_actual_cli_requires_object_in_explicit_role_profiles_file(tmp_path,text):
    public=tmp_path/'public';public.mkdir();goal=public/'goal';goal.write_text('Original goal')
    config=tmp_path/'profiles.json';config.write_text(text)
    root=tmp_path/'instance'
    environment={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[2]/'src')}
    result=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.cli','prepare',
        '--root',str(root),'--objective',str(goal),'--public-root',str(public),
        '--native-launcher-json','["never-launched"]','--max-tokens','1000','--max-seconds','120',
        '--max-steps','8','--role-profiles',str(config)],env=environment,capture_output=True,text=True,timeout=10)
    response=json.loads(result.stdout)
    if text=='{}':
        assert result.returncode==0, result.stderr+result.stdout
        assert response['status']=='prepared'
        assert c.settings(root)['role_profiles']==profiles.normalize({},DEFAULTS)
        assert not (root/'jobs').exists()
    else:
        assert result.returncode==1, result.stderr+result.stdout
        assert response==dict(status='error',error='Role profiles file must contain a JSON object')
        assert not root.exists()


@pytest.mark.parametrize('value',[[], {'Root':{}}, {'H':None}, {'A':{'unknown':'x'}},
    {'W':{'model':''}}, {'E':{'reasoning_effort':False}}, {'A':{'service_tier':None}}])
def test_invalid_role_configuration_rejected_before_instance_creation(tmp_path,value):
    with pytest.raises(ValueError,match='Role profile'):
        prepare(tmp_path,role_profiles=value)
    assert not (tmp_path/'instance').exists()


def test_sparse_fields_and_unlisted_model_names_are_explicitly_inherited():
    overrides={'A':{'model':'future-model','service_tier':'priority'}}
    result=profiles.normalize(overrides,DEFAULTS)
    assert result=={role:dict(DEFAULTS,**overrides.get(role,{})) for role in 'HWAE'}
    settings=dict(DEFAULTS,role_profiles=overrides)
    assert {role:profiles.selected(settings,role) for role in 'HWAE'}==result
    with pytest.raises(ValueError,match='Unknown scientific role'):
        profiles.selected(DEFAULTS,'Root')


def test_global_profile_is_bound_to_new_intent_and_legacy_intent_stays_readable(tmp_path):
    root=prepare(tmp_path)
    settings=c.settings(root);job=c.build_job(root,1,'H',[])
    assert 'role_profiles' not in settings and 'native_profile' not in job
    assert profiles.binding(settings,job)==dict(native_profile=DEFAULTS)
    assert profiles.validate_intent(settings,job,{})==DEFAULTS


@pytest.mark.parametrize('damage',['job','intent','missing_intent'])
def test_changed_profile_rejected_before_native_submission(tmp_path,monkeypatch,damage):
    from scidiscovery.research_loop import accepted_budget
    root=prepare(tmp_path,role_profiles=OVERRIDES)
    job=c.build_job(root,1,'H',[])
    intent=dict(task_ref=c.record_ref(root/'jobs/001.json'),native_profile=job['native_profile'])
    if damage=='job':
        job['native_profile']=dict(DEFAULTS,model='different')
        c.write(root/'jobs/001.json',job);intent['task_ref']=c.record_ref(root/'jobs/001.json')
    elif damage=='intent':intent['native_profile']=dict(DEFAULTS,service_tier='different')
    else:intent.pop('native_profile')
    accepted_budget.accept_native(root,1,execution.origin(root,1),intent)
    monkeypatch.setattr(native.subprocess,'Popen',lambda *a,**kw:pytest.fail('must reject before submission'))
    with pytest.raises(ValueError,match='profile changed'):native.run_native(root,1)


@pytest.mark.process_e2e
def test_actual_fresh_and_resumed_argv_keep_original_role_profile(tmp_path,monkeypatch):
    from scidiscovery.research_loop import accepted_budget
    from test_native_cli import metering_instance

    global_profile=dict(model='fixture-no-model',reasoning_effort='high',service_tier='default')
    expected=dict(model='role-fixture-no-model',reasoning_effort='medium',service_tier='priority')
    original=controller.prepare
    def configured(*args,**kwargs):
        return original(*args,**dict(kwargs,**global_profile,role_profiles={'H':expected}))
    monkeypatch.setattr(controller,'prepare',configured)
    tokens=dict(input_tokens=10,cached_input_tokens=3,output_tokens=4)
    root=metering_instance(tmp_path,monkeypatch,'resume',baseline=tokens,delta=tokens)
    assert controller.run(root)['status']=='technical_pause'
    first=resume.wait_runner(root)
    work=c.workspace(root,1)
    originals={path:path.read_bytes() for path in (root/'settings.json',root/'jobs/001.json',
        work/'science-task.json',first/'science-result.schema.json',first/'native-intent.json',
        first/'native-outcome.json',first/'usage.json',first/'native.jsonl')}
    result=continuation.start(root,1,after_interrupt=True,expected_attempt=0,
        native_intent_ref=c.record_ref(first/'native-intent.json'))
    assert result['role']=='H'
    current=resume.wait_runner(root)
    settings=c.settings(root)
    assert {key:settings[key] for key in global_profile}==global_profile
    assert profiles.selected(settings,'H')==expected
    assert c.read(root/'jobs/001.json')['native_profile']==expected
    for record in (first,current):
        intent=c.read(record/'native-intent.json')
        assert intent['native_profile']==expected
        assert intent['task_ref']==c.record_ref(root/'jobs/001.json')
        outcome=c.read(record/'native-outcome.json')
        assert outcome['native_identity']['session']['source']=='native_metadata_hooks'
        assert outcome['native_identity']['session']['thread_id']=='metering-thread'
        assert outcome['budget_notification']['status']=='available'
        assert c.read(record/'usage.json')==tokens
    launches=[json.loads(line) for line in (work/'work/argv.jsonl').read_text().splitlines()]
    assert len(launches)==2
    for args in launches:
        configs=[args[index+1] for index,value in enumerate(args) if value=='-c']
        assert configs.count('model_reasoning_effort='+json.dumps(expected['reasoning_effort']))==1
        assert configs.count('service_tier='+json.dumps(expected['service_tier']))==1
    requests=[json.loads(line) for line in (work/'work/requests.jsonl').read_text().splitlines()]
    assert [request['method'] for request in requests]==['thread/start','turn/start','thread/resume','turn/start']
    for request in requests:
        assert request['params']['model']==expected['model']
        assert request['params']['serviceTier']==expected['service_tier']
        if request['method']=='turn/start':
            assert request['params']['effort']==expected['reasoning_effort']
            assert request['params']['threadId']=='metering-thread'
    assert requests[2]['params']['threadId']=='metering-thread' and requests[2]['params']['excludeTurns'] is True
    outcome=c.read(current/'native-outcome.json')
    assert outcome['returncode']==0 and outcome['terminal_observed'] is True and outcome['stop_reason'] is None
    assert outcome['metering']['coverage']=='terminal' and outcome['metering']['reconciliation']=='matched'
    assert (current/'native.jsonl').read_bytes().endswith(b'{"type":"turn.completed","native_terminal":true}\n')
    assert (current/'science-output.json').read_bytes()==(work/'work/resumed-result.json').read_bytes()
    assert native.usage(root)['observed_tokens']==28
    head=accepted_budget.read(root)
    assert head['max_local_step']==1 and head['dispatch_count']==2 and head['continuation_count']==0
    assert c.read(current/'native-intent.json')['continuation_kind']=='reentry'
    assert budget.used_steps(root)==1 and len(execution.records(root,1))==2 and not (root/'jobs/002.json').exists()
    assert all(path.read_bytes()==data for path,data in originals.items())
    captured={path:path.read_bytes() for path in (work/'work/argv.jsonl',work/'work/requests.jsonl')}
    assert native.wait_native(root,1)==result
    assert all(path.read_bytes()==data for path,data in captured.items())
    assert (work/'work/launches').read_text()=='exec\nresume\n'


@pytest.mark.process_e2e
@pytest.mark.parametrize('tier',['default','priority'])
@pytest.mark.parametrize('kind',['raw_codex','wrapper'])
def test_global_configuration_reaches_actual_launcher_without_role_model_overrides(tmp_path,monkeypatch,kind,tier):
    from scidiscovery.research_loop import accepted_budget

    if kind=='raw_codex':
        from test_native_cli import metering_instance
        original=controller.prepare
        def configured(*args,**kwargs):
            return original(*args,**dict(kwargs,model='no-model',reasoning_effort='medium',service_tier=tier))
        monkeypatch.setattr(controller,'prepare',configured)
        root=metering_instance(tmp_path,monkeypatch,'profile')
        assert native.execute_native(root,1)['role']=='H'
        work=c.workspace(root,1)
        launches=[json.loads(line) for line in (work/'work/argv.jsonl').read_text().splitlines()]
        assert len(launches)==1
        args=launches[0]
        configs=[args[index+1] for index,value in enumerate(args) if value=='-c']
        assert configs.count('model_reasoning_effort="medium"')==1
        assert configs.count('service_tier='+json.dumps(tier))==1
        assert not any(config.startswith('mcp_servers.') for config in configs)
        requests=[json.loads(line) for line in (work/'work/requests.jsonl').read_text().splitlines()]
        assert [request['method'] for request in requests]==['thread/start','turn/start']
        for request in requests:
            assert request['params']['model']=='no-model'
            assert request['params']['serviceTier']==tier
        assert requests[1]['params']['effort']=='medium'
        # Ordinary standard-Codex jobs select prompt transport in Codex.for_job.
        outcome=c.read(execution.origin(root,1)/'native-outcome.json')
        assert outcome['native_schema_transport']=='prompt'
        assert '--output-schema' not in args and 'outputSchema' not in requests[1]['params']
        assert outcome['returncode']==0 and outcome['terminal_observed'] is True and outcome['stop_reason'] is None
        assert outcome['budget_notification']['status']=='available'
        assert outcome['native_identity']['session']['source']=='native_metadata_hooks'
        assert outcome['metering']['coverage']=='terminal' and outcome['metering']['reconciliation']=='matched'
        assert native.usage(root)['observed_tokens']==10
        settings=c.settings(root)
        assert settings['role_profile_declarations']=={'W':{'subagents':False}}
        expected=dict(model='no-model',reasoning_effort='medium',service_tier=tier)
        assert {role:profiles.selected(settings,role) for role in 'HWAE'}=={role:expected for role in 'HWAE'}
        assert accepted_budget.read(root)['dispatch_count']==1 and not (root/'jobs/002.json').exists()
        return

    public=tmp_path/'public';public.mkdir();goal=public/'goal';goal.write_text('Bounded transport fixture.')
    script=tmp_path/('codex' if kind=='raw_codex' else 'wrapper.py')
    script.write_text('#!'+sys.executable+'\n'+r'''import json,os,pathlib,sys
args=sys.argv[1:];sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
(work/'work/argv.json').write_text(json.dumps(args))
payload=dict(summary='Bounded fixture',scientific_result='No model called.',limitations=[],
 implementation_selection=None,search_plan=None,next_action='wait',route_reason='Fixture complete')
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=7,output_tokens=3))),flush=True)
''')
    script.chmod(0o755)
    launcher=[str(script)] if kind=='raw_codex' else [sys.executable,str(script)]
    root=tmp_path/'instance'
    controller.prepare(root,goal,public,launcher,max_tokens=1000,max_seconds=60,max_steps=2,
        model='no-model',reasoning_effort='medium',service_tier=tier,search_policy=None)
    job=c.build_job(root,1,'H',[])
    record=execution.record(root,1)
    intent=dict(step=1,task_ref=c.record_ref(root/'jobs/001.json'),**profiles.binding(c.settings(root),job))
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'advance','profile_fixture')
    anchor=budget.accept_node(root,job)
    if anchor is not None:intent['budget_slice']=anchor
    accepted_budget.accept_native(root,1,record,intent)
    native.run_native(root,1)
    assert not (root/'jobs/002.json').exists()
    assert 'role_profiles' not in c.settings(root)
    args=c.read(c.workspace(root,1)/'work/argv.json')
    configs=[args[i+1] for i,value in enumerate(args) if value=='-c']
    assert args[args.index('--model')+1]=='no-model'
    assert configs.count('model_reasoning_effort="medium"')==1
    assert configs.count('service_tier='+json.dumps(tier))==1
    assert ('--output-schema' in args)==(kind=='raw_codex')
    assert not any(config.startswith('mcp_servers.') for config in configs)
