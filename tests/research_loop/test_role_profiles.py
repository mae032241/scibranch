"""Frozen heterogeneous native configuration with local stand-ins; no model calls."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, continuation, execution, native, profiles, runtime
from scidiscovery.research_loop.cli import main
import test_worker_resume as resume
from test_rootless_fragments import accepted, launcher, arguments, next_session, resume_arguments, spec


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


def test_no_configuration_preserves_legacy_job_and_intent_binding(tmp_path):
    root=prepare(tmp_path)
    settings=c.settings(root);job=c.build_job(root,1,'H',[])
    assert 'role_profiles' not in settings and 'native_profile' not in job
    assert profiles.binding(settings,job)=={}
    assert profiles.validate_intent(settings,job,{})==DEFAULTS


@pytest.mark.parametrize('damage',['job','intent','missing_intent'])
def test_changed_profile_rejected_before_native_submission(tmp_path,monkeypatch,damage):
    root=prepare(tmp_path,role_profiles=OVERRIDES)
    job=c.build_job(root,1,'H',[])
    intent=dict(task_sha256=c.digest(root/'jobs/001.json'),native_profile=job['native_profile'])
    if damage=='job':
        job['native_profile']=dict(DEFAULTS,model='different')
        c.write(root/'jobs/001.json',job);intent['task_sha256']=c.digest(root/'jobs/001.json')
    elif damage=='intent':intent['native_profile']=dict(DEFAULTS,service_tier='different')
    else:intent.pop('native_profile')
    c.write(execution.origin(root,1)/'native-intent.json',intent)
    monkeypatch.setattr(native.subprocess,'Popen',lambda *a,**kw:pytest.fail('must reject before submission'))
    with pytest.raises(ValueError,match='profile changed'):native.run_native(root,1)


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
    assert controller.run(root)['status']=='wait'
    assert 'role_profiles' not in c.settings(root)
    args=c.read(c.workspace(root,1)/'work/argv.json')
    configs=[args[i+1] for i,value in enumerate(args) if value=='-c']
    assert args[args.index('--model')+1]=='no-model'
    assert configs.count('model_reasoning_effort="medium"')==1
    assert configs.count('service_tier='+json.dumps(tier))==1
    assert ('--output-schema' in args)==(kind=='raw_codex')
    assert not any(config.startswith('mcp_servers.') for config in configs)


def test_launcher_rejects_configuration_mismatch_before_writes(launcher,accepted):
    root,job,record=accepted
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    args=arguments(root,record)
    with pytest.raises(ValueError,match='launcher_role_profiles_mismatch'):
        launcher.native_args(dict(spec(root),role_profiles={'A':{'model':'different'}}),c.workspace(root,1),args)
    assert before=={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_launcher_generated_config_uses_same_profile(launcher,tmp_path,monkeypatch):
    root=prepare(tmp_path,role_profiles={'A':dict(model='gpt-5.6-sol',reasoning_effort='medium',service_tier='priority')})
    job=c.build_job(root,1,'A',[]);w=c.workspace(root,1);h=root/'native-homes/001'
    monkeypatch.setenv('THIN_HWA_ROLE','A');monkeypatch.setenv('THIN_HWA_JOB_MANIFEST',str(root/'jobs/001.json'))
    s=dict(c.settings(root),scene_root=str(root),runs_dir=str(root/'runs'),submission_dir=str(root/'submission'),
        native='/usr/bin/true',code_mode_host={'path':'/usr/bin/true'},runtime_paths=[],profile='isolated',
        task_python=sys.executable,scid_python=sys.executable)
    launcher.prepare_home(s,w,h)
    import tomllib
    generated=tomllib.loads((h/'config.toml').read_text())
    profile=job['native_profile']
    assert generated['model']==profile['model']
    assert generated['model_reasoning_effort']==profile['reasoning_effort']
    assert generated['service_tier']==profile['service_tier']
    assert generated['permissions']['isolated']['network']['enabled'] is False


@pytest.fixture
def profiled_accepted(tmp_path,monkeypatch,request):
    original=controller.prepare
    def configured(*args,**kwargs):
        return original(*args,**dict(kwargs,role_profiles={'W':dict(model='gpt-6.1-sol',reasoning_effort='medium',service_tier='priority')}))
    monkeypatch.setattr(controller,'prepare',configured)
    root,job,record=accepted.__wrapped__(tmp_path,monkeypatch,request)
    intent=c.read(record/'native-intent.json')
    c.write(record/'native-intent.json',dict(intent,native_profile=job['native_profile']))
    return root,job,record


@pytest.mark.parametrize('profiled_accepted',[None,'fragment'],indirect=True)
def test_launcher_accepts_exact_profile_argv_for_fresh_and_resume(launcher,profiled_accepted,monkeypatch,request):
    root,job,first=profiled_accepted
    if request.node.callspec.params['profiled_accepted']:
        record=next_session(root,job,first,monkeypatch)
        intent=c.read(record/'native-intent.json')
        c.write(record/'native-intent.json',dict(intent,native_profile=job['native_profile']))
        binding=execution.identity(root,1,1)
        c.write(record/'runner-process.json',runtime.process_identity(os.getppid(),binding))
        c.write(record/'native-process.json',runtime.process_identity(os.getpid(),binding))
        args=resume_arguments(root,record)
    else:
        record=first;args=arguments(root,record)
    declared=dict(spec(root),role_profiles={'W':job['native_profile']})
    result=launcher.native_args(declared,c.workspace(root,1),args)
    assert result[result.index('--model')+1]=='gpt-6.1-sol'
    assert 'service_tier="priority"' in result and 'model_reasoning_effort="medium"' in result
    for wrong in ('model_reasoning_effort="high"','service_tier="default"'):
        changed=list(args);key=wrong.split('=')[0]
        index=next(i for i,v in enumerate(changed) if v.startswith(key+'='));changed[index]=wrong
        with pytest.raises(ValueError,match='native_config_override_rejected'):
            launcher.native_args(declared,c.workspace(root,1),changed)
