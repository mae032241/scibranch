"""Default local execution owns its receiver; only model communication is fake."""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

import pytest

from scidiscovery.research_loop import budget, contracts as c, execution, experiments as e, native, runtime
from scidiscovery.research_loop import controller
from test_history_handoff import finish

pytestmark = pytest.mark.process_e2e


SCIENTIFIC_SOURCE = '''import json,os,pathlib,socket
here=pathlib.Path.cwd()
probe=json.loads((here/'probe.json').read_text()) if (here/'probe.json').exists() else json.loads((here/'work/probe.json').read_text())
assert not pathlib.Path(probe['private']).exists()
assert not pathlib.Path(probe['sibling']).exists()
public=pathlib.Path(probe['public'])
assert public.read_text()=='declared public input'
try:public.write_text('must stay read-only')
except OSError:pass
else:raise AssertionError('Declared public input was writable')
assert public.read_text()=='declared public input'
try:(public.parent/'private-link').read_text()
except OSError:pass
else:raise AssertionError('Public symlink exposed a private sibling')
assert not (pathlib.Path(os.environ['CODEX_HOME'])/'auth.json').exists()
configuration=(pathlib.Path(os.environ['CODEX_HOME'])/'config.toml').read_text()
assert 'model_provider' not in configuration and 'model=' not in configuration
assert not (pathlib.Path(probe['workspace'])/'work/experiment/submit.json').exists()
try:
 connection=socket.create_connection(('127.0.0.1',probe['port']),timeout=.5)
except OSError:connected=False
else:connected=True;connection.close()
assert connected==probe['network_enabled'],(connected,probe['network_enabled'])
if probe['directory']:
 assert (here/'input.txt').read_text()=='accepted scientific input'
 try:(here/'input.txt').write_text('must stay read-only')
 except OSError:pass
 else:raise AssertionError('Accepted input was writable')
assert all((here/name).is_dir() for name in ('.git','.agents','.codex','.aws'))
for name in ('.git','.agents','.codex','.aws'):
 try:(here/name/'must-not-write').write_text('protected native metadata')
 except OSError:pass
 else:raise AssertionError('Native metadata was writable: '+name)
if probe.get('exclude_tmpdir'):
 try:(pathlib.Path(os.environ['TMPDIR'])/'must-not-write').write_text('excluded backend temporary root')
 except OSError:pass
 else:raise AssertionError('Excluded backend TMPDIR was writable')
(here/'artifacts/sentinel.txt').write_text('real generic backend completed')
(here/'artifacts/public-observed.txt').write_text(public.read_text())
(here/'artifacts/result.json').write_text(json.dumps(dict(version=1,metrics=[dict(name='sentinel',value=1)],outputs=[dict(path='artifacts/sentinel.txt',label='inert sentinel')])))
print('real-backend-sentinel',flush=True)
'''

SLEEPING_SOURCE = '''import pathlib,subprocess,sys,time
here=pathlib.Path.cwd()
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
(here/'artifacts/backend-started').write_text('An inert parent and child are sleeping.')
time.sleep(60)
(here/'artifacts/backend-finished').write_text('Must not finish after native lifetime ends.')
child.wait()
'''


NATIVE_STANDIN = '''import json,os,pathlib,sys,time
args=sys.argv[1:];prompt=sys.stdin.read()
assert 'worker_execution_bridge' in prompt
assert args[args.index('--sandbox')+1]=='workspace-write' and '-P' not in args
assert any(value.startswith('sandbox_workspace_write=') for index,value in enumerate(args) if index and args[index-1]=='-c')
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
probe=json.loads((work/'work/probe.json').read_text())
slot=work/'work/experiment'
assert slot.is_dir(),'The native host must own the submission receiver'
def request(number,arguments):
 value=dict(submission_ref='S%03d'%number,arguments=arguments)
 temporary=slot/'standin.pending';temporary.write_text(json.dumps(value));temporary.replace(slot/'submit.json')
 deadline=time.monotonic()+25
 while time.monotonic()<deadline:
  try:reply=json.loads((slot/'response.json').read_text())
  except (FileNotFoundError,json.JSONDecodeError):time.sleep(.02);continue
  if reply.get('request')==value:return reply
  time.sleep(.02)
 raise AssertionError('Default native local submission has no host receiver')
assert request(1,['capabilities'])['code']==0
source=work/('experiments/sentinel/run.py' if probe['directory'] else 'run.py')
arguments=['run','--script',str(source)]
if probe['directory']:arguments+=['--experiment-dir',str(source.parent)]
if probe.get('lifecycle_exit'):
 submission=dict(submission_ref='S002',arguments=arguments)
 temporary=slot/'standin.pending';temporary.write_text(json.dumps(submission));temporary.replace(slot/'submit.json')
 deadline=time.monotonic()+25
 while not (work/'work/release-native').exists():
  assert time.monotonic()<deadline,'Host did not release the lifecycle fixture'
  time.sleep(.02)
 sys.exit(0)
reply=request(2,arguments)
assert reply['code']==0,reply
assert reply['result']['status']=='known_success',reply
call=reply['result']['call_id']
assert request(3,['query','--call-id',call])['result']['state']=='succeeded'
collected=request(4,['collect','--call-id',call]);assert collected['code']==0,collected
if probe['directory']:
 outputs=collected['result']['outputs']
 assert any(pathlib.Path(item['path']).read_text()=='real generic backend completed' for item in outputs if item['path'].endswith('sentinel.txt'))
assert request(5,['lineage-status'])['code']==0
(work/'artifacts/transport.json').write_text(json.dumps(dict(call_id=call,provider_calls=0)))
payload=dict(summary='Engineering transport fixture',scientific_result='The inert generic process completed; no scientific validity claim.',limitations=['No model provider or scientific solver ran.'],implementation_selection=None,stage_intent='review',mechanism_findings=[dict(mechanism='Fixture mechanism',basis='observed',finding='An inert generic process completed.')])
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
print(json.dumps(dict(type='thread.started',thread_id='11111111-1111-4111-8111-111111111111')),flush=True)
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=1,cached_input_tokens=0,output_tokens=1))),flush=True)
'''


def actual_native():
    entry=shutil.which('codex')
    package=Path(entry).resolve().parent.parent if entry else Path('/unavailable')
    vendor=package/'node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex'
    actual=Path(os.environ.get('SCID_TEST_CODEX_NATIVE',str(vendor)))
    if 'SCID_TEST_CODEX_NATIVE' in os.environ:
        assert actual.is_file() and shutil.which('bwrap'), 'Explicit real-native verification requirements are missing'
    if not actual.is_file() or shutil.which('bwrap') is None:
        pytest.skip('Actual installed native sandbox and bwrap are required')
    return actual.resolve()


def _default_local_case(tmp_path, monkeypatch, directory, network_enabled, *, exclude_tmpdir=False,lifecycle_exit=False):
    actual=actual_native()
    home=tmp_path/'codex-home';home.mkdir()
    # Frozen framework permissions take precedence over a conflicting native home.
    (home/'config.toml').write_text('[sandbox_workspace_write]\nnetwork_access='+str(not network_enabled).lower()
        +'\nexclude_tmpdir_env_var='+str(exclude_tmpdir).lower()+'\n')
    (home/'auth.json').write_text('{"private":"must never enter scientific backend"}')
    binary=tmp_path/'bin';binary.mkdir()
    package=tmp_path/'node_modules/@openai/codex';(package/'bin').mkdir(parents=True)
    entry=package/'bin/codex.js';entry.write_text('#!'+sys.executable+'\n'+NATIVE_STANDIN);entry.chmod(0o555)
    fake=binary/'codex';fake.symlink_to(entry)
    architecture,target={'x86_64':('x64','x86_64-unknown-linux-musl'),
        'aarch64':('arm64','aarch64-unknown-linux-musl')}[os.uname().machine]
    installed=package/'node_modules/@openai'/('codex-linux-'+architecture)/'vendor'/target/'bin/codex'
    installed.parent.mkdir(parents=True);installed.symlink_to(actual)
    # Only outer model communication is replaced. Discovery follows this
    # selected npm installation without any session-created helper in PATH.
    monkeypatch.setenv('CODEX_HOME',str(home))
    monkeypatch.setenv('PATH',str(binary)+os.pathsep+'/usr/bin:/bin')
    assert shutil.which('codex-linux-sandbox') is None
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md'
    objective.write_text('Engineering transport only; no scientific qualification.')
    (public/'sentinel.txt').write_text('declared public input')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[str(fake)],max_tokens=10000,max_seconds=90,max_steps=4,
        model='no-model',reasoning_effort='high',search_policy=None,node_budget_mode='soft',
        execution_config=(dict(executor='local_execution:command',
            configuration=dict(run_argv=[sys.executable],max_seconds=3)) if lifecycle_exit else None),
        native_options=dict(network=network_enabled,
            sandbox_workspace_write=dict(exclude_tmpdir_env_var=exclude_tmpdir)))
    assert c.settings(root)['experiment_mode']=='local'
    assert c.settings(root)['native_options']['network']==network_enabled
    c.write(root/'host-started.json',budget.clock_stamp());budget.transition(root,'advance','Inert local transport check')
    finish(root,1,'H',[],action='experiment')
    c.build_job(root,2,'W',[1]);work=c.workspace(root,2)
    if exclude_tmpdir:
        temporary=work/'work/native-tmp';temporary.mkdir()
        monkeypatch.setenv('TMPDIR',str(temporary))
    sibling=tmp_path/'sibling-private.txt';sibling.write_text('Unrelated private evidence')
    (public/'private-link').symlink_to(sibling)
    # The sentinel is outside every pre-existing scientific runtime/workspace
    # mount: this absolute read fails on the original missing-public mapping.
    assert not public.is_relative_to(work) and not work.is_relative_to(public)
    assert not any(public.is_relative_to(Path(path).resolve()) for path in
        ('/usr','/bin','/lib','/lib64',sys.base_prefix,sys.prefix))
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1',0));listener.listen(8)
        probe=dict(private=str(root/'jobs/002.json'),sibling=str(sibling),workspace=str(work),
            public=str(Path(c.read(root/'jobs/002.json')['public_root'])/'sentinel.txt'),
            port=listener.getsockname()[1],network_enabled=network_enabled,directory=directory,
            exclude_tmpdir=exclude_tmpdir,lifecycle_exit=lifecycle_exit)
        c.write(work/'work/probe.json',probe)
        target=work/'experiments/sentinel' if directory else work
        target.mkdir(parents=True,exist_ok=True)
        if directory:
            c.write(target/'probe.json',probe);(target/'input.txt').write_text('accepted scientific input')
        (target/'run.py').write_text(SLEEPING_SOURCE if lifecycle_exit else SCIENTIFIC_SOURCE)
        if lifecycle_exit:
            _check_active_backend_shutdown(root,work,target)
            return
        # No test-owned serve/context/thread may complete this path.
        try:native.execute_native(root,2)
        except Exception as error:
            record=execution.origin(root,2)
            paths=[record/name for name in ('native.stderr','runner.stderr','native.jsonl','native-outcome.json','native-execution-permissions.json')]
            paths.extend(root.glob('host-runtime/experiment-control/*/bridge-diagnostics/latest-error.json'))
            paths.extend(root.glob('host-runtime/experiment-control/**/stderr.raw'))
            diagnostic=[]
            for path in paths:
                if path.is_file():diagnostic.append(path.relative_to(root).as_posix()+':\n'+path.read_text(errors='replace')[-6000:])
            pytest.fail(type(error).__name__+': '+str(error)+'\n'+'\n'.join(diagnostic))
    transport=c.read(work/'artifacts/transport.json');assert transport['provider_calls']==0
    record=execution.origin(root,2);config=c.read(record/'experiment-config.json')
    calls=e.Records(config).calls();assert len(calls)==1
    accepted=e.Records(config).accepted(transport['call_id']);call=e.Records(config).directory(accepted['call_id'])
    assert b'real-backend-sentinel' in (call/'stdout.raw').read_bytes()
    assert c.read(call/'completed.json')['status']=='known_success'
    assert (public/'sentinel.txt').read_text()=='declared public input'
    assert (call/'outputs/public-observed.txt' if directory else work/'artifacts/public-observed.txt').read_text()=='declared public input'
    if directory:
        assert (call/'outputs/sentinel.txt').read_text()=='real generic backend completed'
        assert (target/'input.txt').read_text()=='accepted scientific input'
        assert not (target/'artifacts/sentinel.txt').exists()
    else:assert (work/'artifacts/sentinel.txt').read_text()=='real generic backend completed'
    snapshot=c.read(record/'native-execution-permissions.json')
    assert snapshot['policy']['network_access']==network_enabled
    assert snapshot['permission_profile']['configuration']['network']['enabled']==network_enabled
    assert snapshot['permission_profile']['configuration']['extends']==':workspace'
    if exclude_tmpdir:
        assert snapshot['permission_profile']['configuration']['filesystem']=={}
        assert snapshot['backend_permission_profile']['configuration']['filesystem']=={':tmpdir':'read'}
    assert str(Path.home()/'.codex/config.toml') not in {item['path'] for item in snapshot['permission_sources']}
    assert snapshot['native']['path']==str(actual)
    assert not (record/'scientific-native-home/auth.json').exists()
    for path in (work/'work/experiment').glob('*.json'):
        assert re.search(rb'[0-9a-f]{64}',path.read_bytes()) is None
    # Completion ends the original receiver. A later ordinary file never
    # creates another call or replays the completed experiment.
    slot=work/'work/experiment';response=(slot/'response.json').read_bytes()
    c.write(slot/'submit.json',dict(submission_ref='S999',arguments=['run','--script',str(target/'run.py')]))
    time.sleep(.25)
    assert (slot/'response.json').read_bytes()==response and len(e.Records(config).calls())==1


@pytest.mark.parametrize('directory',[True,False],ids=['directory-snapshot','source-only'])
@pytest.mark.parametrize('network_enabled',[False,True],ids=['declared-network-denied','declared-network-allowed'])
def test_execute_native_default_local_owns_real_generic_backend(tmp_path,monkeypatch,directory,network_enabled):
    _default_local_case(tmp_path,monkeypatch,directory,network_enabled)


def test_real_backend_excluded_tmpdir_keeps_native_workspace_grant(tmp_path,monkeypatch):
    _default_local_case(tmp_path,monkeypatch,False,False,exclude_tmpdir=True)


def _process_tree(identity):
    observed={}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():continue
        try:current=runtime.process_identity(int(entry.name),identity['task_sha256'])
        except (OSError,ValueError,IndexError):continue
        if current['state']!='Z':observed[current['pid']]=current
    owned={identity['pid']:identity}
    while True:
        children={pid:value for pid,value in observed.items() if value['ppid'] in owned and pid not in owned}
        if not children:return list(owned.values())
        owned.update(children)


def _check_active_backend_shutdown(root,work,target):
    errors=[]
    def original_call():
        try:native.execute_native(root,2)
        except Exception as error:errors.append(error)
    original=threading.Thread(target=original_call,daemon=True)
    original.start();identities=[];record=execution.origin(root,2)
    release=work/'work/release-native'
    try:
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            path=record/'experiment-config.json'
            if path.exists():
                config=c.read(path);calls=e.Records(config).calls()
                if len(calls)==1 and (calls[0]/'process.json').exists() and (work/'artifacts/backend-started').exists():break
            if not original.is_alive():pytest.fail('Native stopped before the real backend started: '+repr(errors))
            time.sleep(.02)
        else:pytest.fail('The real sleeping backend did not publish its original process and start marker')
        assert len(calls)==1
        call=calls[0];accepted=(call/'accepted.json').read_bytes()
        identity=c.read(call/'process.json');identities=_process_tree(identity)
        assert len(identities)>1 and all(runtime.process_matches(item,item['task_sha256']) for item in identities)
        from scidiscovery.research_loop import worker_execution_bridge
        assert config['max_seconds']==3
        with pytest.raises(BlockingIOError):
            worker_execution_bridge.stopped(record,execution.identity(root,2))
        release.touch();original.join(timeout=15)
        assert not original.is_alive(),'Native cleanup did not finish'
        assert errors,'An early native exit cannot seal a scientific result'
        assert not (record/'result-identity.json').exists()
        assert not (work/'artifacts/backend-finished').exists()
        deadline=time.monotonic()+3
        while any(runtime.process_matches(item,item['task_sha256']) for item in identities) and time.monotonic()<deadline:time.sleep(.02)
        alive=[item['pid'] for item in identities if runtime.process_matches(item,item['task_sha256'])]
        assert alive==[],f'Original scientific backend processes survived native exit: {alive}'
        assert len(e.Records(config).calls())==1 and (call/'accepted.json').read_bytes()==accepted
        state=e.Records(config).state(call.name)
        assert state in {'unknown','failed'},state
        if state=='failed':
            terminal=c.read(call/'terminal.json');completed=c.read(call/'completed.json')
            assert terminal['cleanup_confirmed'] is True and terminal['transport_complete'] is True
            assert type(terminal['returncode']) is int and terminal['returncode']!=0
            assert completed['status']=='known_failed'
        # Missing terminal evidence stays unknown; a verified killed process
        # can retain its actual failed exit. Neither state becomes a success.
        retained={path:path.read_bytes() for path in call.rglob('*') if path.is_file()}
        native_process=(record/'native-process.json').read_bytes()
        with pytest.raises(Exception):native.wait_native(root,2)
        assert (record/'native-process.json').read_bytes()==native_process
        assert e.Records(config).state(call.name)==state
        slot=work/'work/experiment';response=(slot/'response.json').read_bytes()
        c.write(slot/'submit.json',dict(submission_ref='S999',arguments=['run','--script',str(target/'run.py')]))
        time.sleep(.25)
        assert (slot/'response.json').read_bytes()==response
        assert len(e.Records(config).calls())==1 and all(path.read_bytes()==data for path,data in retained.items())
    finally:
        release.touch();original.join(timeout=5)
        # A failing negative control must still clean its exact accepted group;
        # cleanup here never turns the earlier survivor assertion into a pass.
        if any(runtime.process_matches(item,item['task_sha256']) for item in identities):
            from scidiscovery.research_loop import local_experiments
            local_experiments.cleanup(root,c.read(root/'jobs/002.json'))


def test_native_exit_cleans_the_active_real_backend_without_replay(tmp_path,monkeypatch):
    _default_local_case(tmp_path,monkeypatch,False,False,lifecycle_exit=True)


@pytest.mark.parametrize('standard',[False,True],ids=['custom-launcher','named-permissions'])
def test_unproven_native_permissions_refuse_managed_launch_without_overrides(tmp_path, standard):
    from scidiscovery.research_loop import native_execution
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('Inert permission negative control.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['custom-launcher'],experiment_mode='local',
        max_tokens=1000,max_seconds=30,max_steps=2,model='no-model',reasoning_effort='high',search_policy=None)
    job=c.build_job(root,1,'W',[]);work=c.workspace(root,1);record=execution.origin(root,1)
    c.write(record/'native-intent.json',dict(task_sha256=c.digest(root/'jobs/001.json')))
    home=tmp_path/'home';home.mkdir()
    (home/'config.toml').write_text('default_permissions="named"\n[permissions.named.network]\nenabled=true\n')
    arguments=['custom-launcher','exec','-']
    applied,launch=native_execution.prepare(root,1,record,work,home,arguments,standard=standard)
    assert applied==arguments
    snapshot=c.read(record/'native-execution-permissions.json')
    scope=dict(job_sha256=snapshot['task_sha256'])
    accepted=dict(native_attempt=snapshot['native_attempt'],scope=scope)
    with pytest.raises(e.UnsupportedExecution,match='unsupported'):
        launch(dict(scope=scope),accepted,['must-never-be-spawned'],str(work))
    assert not list(record.glob('**/process.json'))


@pytest.mark.parametrize('overlap',['control-ancestor','native-home-ancestor','native-home-child','runtime','temporary-mount'])
def test_declared_public_root_overlap_refuses_before_scientific_launch(tmp_path,monkeypatch,overlap):
    from scidiscovery.research_loop import native_execution
    actual=actual_native()
    public=tmp_path/'public';public.mkdir()
    home=tmp_path/'home';home.mkdir()
    if overlap=='control-ancestor':public=tmp_path
    elif overlap=='native-home-ancestor':home=public/'home';home.mkdir()
    elif overlap=='native-home-child':public=home/'public';public.mkdir()
    elif overlap=='runtime':public=Path('/usr')
    elif overlap=='temporary-mount':public=Path('/tmp')
    objective=tmp_path/'objective.md';objective.write_text('Inert public namespace negative control.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[str(actual)],experiment_mode='local',
        max_tokens=1000,max_seconds=30,max_steps=2,model='no-model',reasoning_effort='high',search_policy=None)
    c.build_job(root,1,'W',[]);work=c.workspace(root,1);record=execution.origin(root,1)
    c.write(record/'native-intent.json',dict(task_sha256=c.digest(root/'jobs/001.json')))
    (home/'config.toml').write_text('[sandbox_workspace_write]\nnetwork_access=false\n')
    monkeypatch.setenv('CODEX_HOME',str(home))
    arguments=[str(actual),'exec','--sandbox','workspace-write','-C',str(work),'-']
    _,launch=native_execution.prepare(root,1,record,work,home,arguments,standard=True)
    snapshot=c.read(record/'native-execution-permissions.json')
    assert 'public root overlaps' in snapshot['unsupported']
    scope=dict(job_sha256=snapshot['task_sha256'])
    accepted=dict(native_attempt=snapshot['native_attempt'],scope=scope)
    with pytest.raises(e.UnsupportedExecution,match='public root overlaps'):
        launch(dict(scope=scope),accepted,['must-never-be-spawned'],str(work))
    assert not list(record.glob('**/process.json'))


@pytest.mark.parametrize('exclude_tmpdir,exclude_slash_tmp,tmpdir,workspace,expected',[
    (True,False,'/work/cache','/work',{}),
    (True,False,'/tmp/cache','/work',{}),
    (False,True,'/tmp','/work',{}),
    (False,True,'/tmp/cache','/work',{':slash_tmp':'read'}),
    (True,False,'/separate/cache','/work',{':tmpdir':'read'}),
    (True,True,'/tmp/cache','/tmp/work',{':tmpdir':'read',':slash_tmp':'read'}),
])
def test_tmp_exclusions_preserve_other_legacy_write_roots(exclude_tmpdir,exclude_slash_tmp,tmpdir,workspace,expected):
    from scidiscovery.research_loop import native_execution
    policy=dict(network_access=False,exclude_tmpdir_env_var=exclude_tmpdir,exclude_slash_tmp=exclude_slash_tmp)
    profile=native_execution._profile(policy,Path(workspace),tmpdir,virtual=True)
    assert profile['filesystem']==expected
    assert profile['extends']==':workspace' and profile['network']==dict(enabled=False)


def test_tmp_exclusions_use_each_native_namespace_and_relative_native_tmpdir(tmp_path):
    from scidiscovery.research_loop import native_execution
    work=tmp_path/'work';work.mkdir()
    policy=dict(network_access=True,exclude_tmpdir_env_var=True,exclude_slash_tmp=False)
    # The native TMPDIR inherits a surviving workspace grant; the scientific
    # backend's independent /task-tmp does not. The declaration stays identical.
    parent=native_execution._profile(policy,work,'scratch')
    backend=native_execution._profile(policy,work/'experiments/sentinel','/task-tmp',virtual=True)
    assert parent['filesystem']=={}
    assert backend['filesystem']=={':tmpdir':'read'}
    assert parent['network']==backend['network']==dict(enabled=True)


@pytest.mark.parametrize('resume',[False,True],ids=['exec-parser','exec-resume-parser'])
def test_real_exec_parser_accepts_the_applied_legacy_permission_arguments(tmp_path,monkeypatch,resume):
    from scidiscovery.research_loop import native_execution
    actual=actual_native()
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('No-provider parser check.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[str(actual)],max_tokens=1000,max_seconds=30,max_steps=2,
        model='resume',reasoning_effort='high',search_policy=None)
    c.build_job(root,1,'W',[]);work=c.workspace(root,1);record=execution.origin(root,1)
    c.write(record/'native-intent.json',dict(task_sha256=c.digest(root/'jobs/001.json')))
    home=tmp_path/'home';home.mkdir()
    (home/'config.toml').write_text('[sandbox_workspace_write]\nnetwork_access=false\n')
    monkeypatch.setenv('CODEX_HOME',str(home))
    arguments=[str(actual),'exec','--sandbox','workspace-write','-C',str(work)]
    resume_position=len(arguments) if resume else None
    if resume:arguments.extend(['resume','--json','--model','resume','11111111-1111-4111-8111-111111111111'])
    else:arguments.extend(['--model','resume'])
    arguments.append('-')
    applied,_=native_execution.prepare(root,1,record,work,home,arguments,standard=True,resume_position=resume_position)
    assert '-P' not in applied and '--sandbox' in applied
    assert applied[applied.index('--model')+1]=='resume'
    if resume:
        override=next(index for index,value in enumerate(applied) if value.startswith('sandbox_workspace_write='))
        assert override<applied.index('resume')
    # Help exits in the real argument parser before model/auth communication.
    parsed=subprocess.run([*applied[:-1],'--help'],env=dict(os.environ),capture_output=True,text=True,timeout=10)
    assert parsed.returncode==0,(parsed.stdout,parsed.stderr)
    assert 'Usage:' in parsed.stdout
