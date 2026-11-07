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
    result=profiles.normalize({'A':{'model':'future-model','service_tier':'priority'}},DEFAULTS)
    assert result['A']==dict(DEFAULTS,model='future-model',service_tier='priority')
    assert result['E']==DEFAULTS
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
@pytest.mark.parametrize('role,overrides,tier',[(role,OVERRIDES,'default') for role in 'HWAE'] +
    [('A',{'A':dict(model='gpt-5.6-sol',reasoning_effort='medium',service_tier='priority')},'default'),
     ('H',None,'default'),('H',None,'priority')])
def test_actual_fresh_and_resumed_argv_keep_original_role_profile(tmp_path,monkeypatch,role,overrides,tier):
    original=controller.prepare
    def configured(*args,**kwargs):
        return original(*args,**dict(kwargs,**dict(DEFAULTS,service_tier=tier),role_profiles=overrides))
    monkeypatch.setattr(controller,'prepare',configured)
    fake=resume.FAKE.replace("role!='A'","True").replace("with (work/'work/launches').open('a')",
        "with (work/'work/argv.jsonl').open('a') as log:log.write(json.dumps(args)+'\\n')\nwith (work/'work/launches').open('a')")
    fake=fake.replace("pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))",
        "if role=='W':payload.update(mechanism_findings=None,stage_intent='review')\n"
        + "if role=='A':payload.update(decision_request=None,portfolio_review=None)\n"
        + "pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))")
    root=resume.instance(tmp_path,fake=fake)
    job=c.build_job(root,1,role,[])
    c.write(root/'host-started.json',budget.clock_stamp());budget.transition(root,'advance','profile_fixture')
    with pytest.raises(ValueError,match='unverified'):native.execute_native(root,1)
    first=resume.wait_runner(root)
    assert continuation.start(root,1)['role']==role
    current=resume.wait_runner(root)
    expected=profiles.selected(c.settings(root),role)
    for record in (first,current):
        intent=c.read(record/'native-intent.json')
        if overrides is None:
            assert 'role_profiles' not in c.settings(root) and 'native_profile' not in intent
        else:
            assert intent['native_profile']==expected
    launches=[json.loads(line) for line in (c.workspace(root,1)/'work/argv.jsonl').read_text().splitlines()]
    assert len(launches)==2 and 'resume' not in launches[0] and 'resume' in launches[1]
    for args in launches:
        assert args[args.index('--model')+1]==expected['model']
        assert 'model_reasoning_effort='+json.dumps(expected['reasoning_effort']) in args
        assert 'service_tier='+json.dumps(expected['service_tier']) in args


@pytest.mark.process_e2e
@pytest.mark.parametrize('tier',['default','priority'])
@pytest.mark.parametrize('kind',['raw_codex','wrapper'])
def test_global_configuration_reaches_actual_launcher_without_role_profiles(tmp_path,kind,tier):
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
    c.write(record/'native-intent.json',intent)
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
