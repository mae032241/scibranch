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
source=work/'code/run.py'
arguments=['run','--script',str(source)]
if probe['directory']:arguments+=['--experiment-dir',str(work/'experiments/sentinel')]
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
for item in collected['result']['outputs']:
 retained=pathlib.Path(item['path'])
 for action in (lambda:retained.chmod(0o600),lambda:retained.write_bytes(b'must stay readonly')):
  try:action()
  except OSError:pass
  else:raise AssertionError('Dynamic collected output was writable in the running native process')
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


def _default_local_case(tmp_path, monkeypatch, directory, network_enabled, *, exclude_tmpdir=False,lifecycle_exit=False,
        scientific_python=None):
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
            configuration=dict(run_argv=[str(scientific_python or sys.executable)],max_seconds=3 if lifecycle_exit else 30))
            if lifecycle_exit or scientific_python is not None else None),
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
            c.write(target/'probe.json',probe)
            original=root/'controlled-test-inputs/native-runtime-input.txt'
            original.parent.mkdir();original.write_bytes(b'accepted scientific input')
            slot=work/'inputs/native-runtime-input.txt'
            c._bind_original(original,slot,root=root,size_bytes=original.stat().st_size,source_kind='mutable')
            # Preserve the existing HEAD alias permission check; this extra
            # runtime read is not an accepted scientific input or qualification.
            os.link(slot,target/'input.txt')
        code=work/'code';code.mkdir()
        source=code/'run.py';source.write_text(SLEEPING_SOURCE if lifecycle_exit else SCIENTIFIC_SOURCE)
        if scientific_python is not None:
            source.write_text('import scid_alternate_runtime_sentinel\n'
                'assert scid_alternate_runtime_sentinel.VALUE == "alternate-only"\n'+source.read_text())
        if lifecycle_exit:
            _check_active_backend_shutdown(root,work,target)
            return
        # No test-owned serve/context/thread may complete this path.
        try:native.execute_native(root,2)
        except Exception as error:
            record=execution.origin(root,2)
            paths=[record/name for name in ('native.stderr','runner.stderr','native.jsonl','native-outcome.json','native-execution-permissions.json')]
            configuration=record/'experiment-config.json'
            if configuration.is_file():
                settings=c.read(configuration);records=e.Records(settings)
                paths.append(records.root.parent/'bridge-diagnostics/latest-error.json')
                paths.extend(call/'stderr.raw' for call in records.calls())
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
    if scientific_python is not None:
        assert snapshot['experiment_python']['run_argv']==[str(scientific_python)]
        assert str(scientific_python.parent.parent) in snapshot['experiment_python']['runtime_roots']
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
    c.write(slot/'submit.json',dict(submission_ref='S999',arguments=['run','--script',str(work/'code/run.py')]))
    time.sleep(.25)
    assert (slot/'response.json').read_bytes()==response and len(e.Records(config).calls())==1


@pytest.mark.parametrize('directory',[True,False],ids=['directory-snapshot','source-only'])
@pytest.mark.parametrize('network_enabled',[False,True],ids=['declared-network-denied','declared-network-allowed'])
@pytest.mark.process_e2e
def test_execute_native_default_local_owns_real_generic_backend(tmp_path,monkeypatch,directory,network_enabled):
    _default_local_case(tmp_path,monkeypatch,directory,network_enabled)


@pytest.mark.process_e2e
def test_real_backend_excluded_tmpdir_keeps_native_workspace_grant(tmp_path,monkeypatch):
    _default_local_case(tmp_path,monkeypatch,False,False,exclude_tmpdir=True)


@pytest.mark.process_e2e
def test_real_backend_imports_only_alternate_venv_module(tmp_path,monkeypatch):
    """One lightweight stdlib-only venv, with real sandbox and fake model I/O."""
    import importlib.util
    import venv
    actual_native()  # Missing real sandbox prerequisites remain an explicit skip.
    assert importlib.util.find_spec('scid_alternate_runtime_sentinel') is None
    environment=tmp_path/'scientific-env'
    venv.EnvBuilder(with_pip=False,symlinks=True).create(environment)
    packages=environment/'lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages'
    (packages/'scid_alternate_runtime_sentinel.py').write_text('VALUE="alternate-only"\n')
    _default_local_case(tmp_path,monkeypatch,False,False,scientific_python=environment/'bin/python')


def _process_tree(identity):
    observed={}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():continue
        try:current=runtime.process_identity(int(entry.name),identity['task_ref'])
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
        assert len(identities)>1 and all(runtime.process_matches(item,item['task_ref']) for item in identities)
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
        while any(runtime.process_matches(item,item['task_ref']) for item in identities) and time.monotonic()<deadline:time.sleep(.02)
        alive=[item['pid'] for item in identities if runtime.process_matches(item,item['task_ref'])]
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
        c.write(slot/'submit.json',dict(submission_ref='S999',arguments=['run','--script',str(work/'code/run.py')]))
        time.sleep(.25)
        assert (slot/'response.json').read_bytes()==response
        assert len(e.Records(config).calls())==1 and all(path.read_bytes()==data for path,data in retained.items())
    finally:
        release.touch();original.join(timeout=5)
        # A failing negative control must still clean its exact accepted group;
        # cleanup here never turns the earlier survivor assertion into a pass.
        if any(runtime.process_matches(item,item['task_ref']) for item in identities):
            from scidiscovery.research_loop import local_experiments
            local_experiments.cleanup(root,c.read(root/'jobs/002.json'))


@pytest.mark.process_e2e
def test_native_exit_cleans_the_active_real_backend_without_replay(tmp_path,monkeypatch):
    _default_local_case(tmp_path,monkeypatch,False,False,lifecycle_exit=True)


@pytest.mark.parametrize('standard',[False,True],ids=['custom-launcher','named-permissions'])
@pytest.mark.process_e2e
def test_unproven_native_permissions_refuse_managed_launch_without_overrides(tmp_path, standard):
    from scidiscovery.research_loop import native_execution
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('Inert permission negative control.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['custom-launcher'],experiment_mode='local',
        max_tokens=1000,max_seconds=30,max_steps=2,model='no-model',reasoning_effort='high',search_policy=None)
    job=c.build_job(root,1,'W',[]);work=c.workspace(root,1);record=execution.origin(root,1)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/001.json')))
    home=tmp_path/'home';home.mkdir()
    (home/'config.toml').write_text('default_permissions="named"\n[permissions.named.network]\nenabled=true\n')
    arguments=['custom-launcher','exec','-']
    applied,launch=native_execution.prepare(root,1,record,work,home,arguments,standard=standard)
    assert applied==arguments
    with pytest.raises(e.UnsupportedExecution,match='unsupported'):
        launch.check_available()
    snapshot=c.read(record/'native-execution-permissions.json')
    scope=dict(job_ref=snapshot['task_ref'])
    accepted=dict(native_attempt=snapshot['native_attempt'],scope=scope)
    with pytest.raises(e.UnsupportedExecution,match='unsupported'):
        launch(dict(scope=scope),accepted,['must-never-be-spawned'],str(work))
    assert not list(record.glob('**/process.json'))


@pytest.mark.parametrize('overlap',['control-ancestor','native-home-ancestor','native-home-child','runtime','temporary-mount'])
@pytest.mark.process_e2e
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
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/001.json')))
    (home/'config.toml').write_text('[sandbox_workspace_write]\nnetwork_access=false\n')
    monkeypatch.setenv('CODEX_HOME',str(home))
    arguments=[str(actual),'exec','--sandbox','workspace-write','-C',str(work),'-']
    _,launch=native_execution.prepare(root,1,record,work,home,arguments,standard=True)
    snapshot=c.read(record/'native-execution-permissions.json')
    assert 'public root overlaps' in snapshot['unsupported']
    scope=dict(job_ref=snapshot['task_ref'])
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
@pytest.mark.process_e2e
def test_real_exec_parser_accepts_the_applied_legacy_permission_arguments(tmp_path,monkeypatch,resume):
    from scidiscovery.research_loop import native_execution
    actual=actual_native()
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('No-provider parser check.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[str(actual)],max_tokens=1000,max_seconds=30,max_steps=2,
        model='resume',reasoning_effort='high',search_policy=None)
    c.build_job(root,1,'W',[]);work=c.workspace(root,1);record=execution.origin(root,1)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/001.json')))
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


# These G1 nodes inspect metadata and argv without executing Git or a backend.
def _fixed_local_code(tmp_path,monkeypatch,*,directory=True):
    """Source/argv fixture only; no Git, namespace or backend is executed."""
    from scidiscovery.research_loop import local_experiments, protocol
    def forbidden(*args,**kwargs):pytest.fail('Pure G1 argv fixture reached a process')
    for name in ('Popen','run','call'):monkeypatch.setattr(subprocess,name,forbidden)
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Engineering argv inspection only.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['argv-only-launcher'],
        max_tokens=1000,max_seconds=30,max_steps=2,model='no-model',reasoning_effort='high',search_policy=None)
    # The existing task producer owns the empty accepted prefix and input facts.
    # This creates no native intent, qualification verdict or execution call.
    job=c.build_job(root,1,'W',[]);work=c.workspace(root,1)
    scope=protocol.stage_scope(root,job)
    code=work/'code';code.mkdir()
    source=code/'run.py';source.write_text("print('fixed source')\n")
    if directory:
        (code/'helper.py').write_text("VALUE='fixed helper'\n")
        (code/'resource.txt').write_text('fixed resource')
    call_id='0001-source-argv'
    version=dict(version=1,object_format='sha1',commit='a'*40,tree_path='.',
        entry='run.py',selection='directory' if directory else 'file',logical_root='code')
    export=root/'host-runtime/code-versions/exports/001'/call_id/'tree';export.mkdir(parents=True)
    for name in ('run.py','helper.py','resource.txt') if directory else ('run.py',):
        (export/name).write_bytes((code/name).read_bytes())
    body=dict(run_argv=[sys.executable],max_seconds=30,cwd=str(work),scope=scope,
        record_dir=str(root/'host-runtime/experiment-control/001/records'))
    interface=dict(version=1,executor='local_execution:command',config_record=body)
    adapter=local_experiments.LocalExecution(body,frozen_interface=interface)
    payload=e.LocalFileDescriptor(name=call_id,local_path=str(export/'run.py'),
        size_bytes=(export/'run.py').stat().st_size,media_type='application/octet-stream')
    e.Records(body).root.joinpath('calls',call_id).mkdir(parents=True)
    runtime_cwd=work/'experiments/sentinel' if directory else work
    runtime_cwd.mkdir(parents=True,exist_ok=True)
    options=dict(source_script=str(source),arguments=['explicit-argument'])
    if directory:options['experiment_directory']=str(runtime_cwd)
    return root,work,adapter,payload,version,export,source,runtime_cwd,options


def _local_code_launch_inputs(tmp_path,fixed):
    from scidiscovery.research_loop import experiment_client, local_experiments, native_execution
    root,work,adapter,payload,version,export,source,cwd,options=fixed
    facts=experiment_client.load_client_facts(adapter.config)
    metadata=adapter.acceptance_metadata(payload,command_options=options,_code_version=version,client_facts=facts)
    accepted=dict(metadata,call_id=payload.name,scope=adapter.config['scope'],code_version=version,snapshot_files=[])
    config=dict(adapter.config,execution_interface=adapter.frozen_interface)
    public=tmp_path/'public'
    home=tmp_path/'native-home';home.mkdir();(home/'config.toml').write_text('')
    policy=dict(writable_roots=[],network_access=False,exclude_tmpdir_env_var=False,exclude_slash_tmp=False)
    value=dict(workspace=str(work),home=str(home),python=sys.executable,runtime_roots=list(dict.fromkeys((sys.base_prefix,sys.prefix))),
        native_attempt=dict(number=0),
        bwrap=dict(path='/usr/bin/bwrap'),native=dict(path='/usr/bin/true'),policy=policy,
        backend_configuration=e.proof(home/'config.toml'),
        backend_permission_profile=dict(configuration=native_execution._profile(policy,cwd,'/task-tmp',virtual=True)))
    if 'experiment_directory' in accepted:
        local_experiments.artifact_directory(config,payload.name).mkdir(parents=True)
    return value,config,accepted,public


def test_local_code_metadata_uses_fixed_payload_and_frozen_runtime_inputs(tmp_path,monkeypatch):
    from scidiscovery.research_loop import experiment_client, local_experiments
    root,work,adapter,payload,version,export,source,cwd,options=_fixed_local_code(tmp_path,monkeypatch)
    from test_mutable_run_inputs import _frozen_material
    logical=cwd/'data.txt'
    material,_,original=_frozen_material(root,work,logical,name='local-argv-data')
    reference=e.proof(original)
    outside,_,_=_frozen_material(root,work,work/'inputs/outside.txt',name='local-outside-data')
    job=c.read(root/'jobs/001.json');job['global_materials'].extend([material,outside])
    c.write(root/'jobs/001.json',job)
    (cwd/'scratch').mkdir();(cwd/'scratch/local.txt').write_text('runtime scratch')
    (cwd/'stdout.log').write_text('runtime log')
    for name in ('artifacts','.git','.agents','.codex','.aws'):
        (cwd/name).mkdir(exist_ok=True);(cwd/name/'local.txt').write_text('runtime state')
    source.unlink()
    load_facts=experiment_client.load_client_facts
    facts=load_facts(adapter.config)
    requested=[];input_reference=experiment_client._observe_input_reference
    def frozen_reference(config,path,*,facts):
        requested.append(Path(path));assert Path(path)==logical
        assert facts is owner_facts
        return input_reference(config,path,facts=facts)
    owner_facts=facts
    def no_inventory(*args,**kwargs):
        pytest.fail('A code version must not inventory its runtime directory')
    monkeypatch.setattr(experiment_client,'_observe_input_reference',frozen_reference)
    monkeypatch.setattr(experiment_client,'load_client_facts',no_inventory)
    monkeypatch.setattr(local_experiments.LocalExecution,'files',no_inventory)
    metadata=adapter.acceptance_metadata(payload,command_options=options,_code_version=version,client_facts=facts)
    assert requested==[logical]
    assert metadata['script']==e.proof(export/'run.py')
    assert metadata['source_script']==str(source)
    assert metadata['command']==[sys.executable,str(source),'explicit-argument']
    assert metadata['cwd']==metadata['experiment_directory']==str(cwd)
    assert metadata['snapshot_files']==[dict(reference,name='data.txt')]
    assert not {'code_version','entry','source_reference','source_input'}&metadata.keys()
    # Readonly scientific material cannot be relabeled as writable artifacts.
    excluded,_,_=_frozen_material(root,work,cwd/'artifacts/frozen.txt',name='local-output-data')
    job['global_materials'].append(excluded);c.write(root/'jobs/001.json',job)
    rejected_facts=load_facts(adapter.config)
    with pytest.raises(ValueError,match='artifacts overlap'):
        adapter.acceptance_metadata(payload,command_options=options,_code_version=version,client_facts=rejected_facts)


@pytest.mark.parametrize('draft',['missing','changed'])
@pytest.mark.parametrize('directory',[False,True],ids=['single-code-file','whole-code-directory'])
def test_local_code_native_argv_uses_whole_fixed_export_and_independent_cwd(tmp_path,monkeypatch,directory,draft):
    from scidiscovery.research_loop import code_versions, local_experiments, native_execution, shared_input_views, experiment_client
    fixed=_fixed_local_code(tmp_path,monkeypatch,directory=directory)
    root,work,adapter,payload,version,export,source,cwd,options=fixed
    value,config,accepted,public=_local_code_launch_inputs(tmp_path,fixed)
    source.unlink()
    if draft=='changed':source.write_text("raise AssertionError('mutable draft must not run')\n")
    (source.parent/'new-draft-sibling.py').write_text("raise AssertionError('draft sibling')\n")
    def no_draft_reference(*args,**kwargs):
        pytest.fail('A code-version launch must not resolve its mutable logical source')
    materialized=[]
    def fixed_export(instance,**kwargs):
        materialized.append((instance,{key:item for key,item in kwargs.items() if key!='context'}))
        return export
    monkeypatch.setattr(experiment_client,'_observe_input_reference',no_draft_reference)
    monkeypatch.setattr(shared_input_views,'ensure_capability',lambda **kwargs:'/usr/bin/bwrap')
    monkeypatch.setattr(code_versions,'materialize',fixed_export)
    argv,host_cwd,finish=native_execution._launch(value,config,accepted,accepted['command'],accepted['cwd'],public=public,root=root)
    assert materialized==[(root,dict(step=1,call_id=payload.name,code_version=version))]
    bindings=[argv[index:index+3] for index,argument in enumerate(argv) if argument in ('--bind','--ro-bind')]
    code_bindings=[(index,row) for index,row in enumerate(bindings) if row[2]==str(work/'code')]
    last_code_index,last_code_binding=code_bindings[-1]
    assert last_code_binding==['--ro-bind',str(export),str(work/'code')]
    assert all(row[0]=='--ro-bind' for _,row in code_bindings)
    assert not any(Path(row[2]).is_relative_to(work/'code') for row in bindings[last_code_index+1:])
    assert not any(row[2]==str(source) for row in bindings)
    assert argv[-len(accepted['command']):]==[sys.executable,str(source),'explicit-argument']
    assert argv[argv.index('--chdir')+1]==str(cwd)
    assert host_cwd is finish is None
    assert (export/'run.py').read_text()=="print('fixed source')\n"
    assert not (export/'new-draft-sibling.py').exists()
    for name in ('.git','.agents','.codex','.aws'):
        assert (cwd/name).is_dir() and not (export/name).exists()
    if directory:
        assert ['--bind',str(local_experiments.artifact_directory(config,payload.name)),str(cwd/'artifacts')] in bindings
        assert (export/'helper.py').read_text()=="VALUE='fixed helper'\n"
        assert (export/'resource.txt').read_text()=='fixed resource'
        assert not (export/'artifacts').exists()


@pytest.mark.parametrize('directory',[False,True],ids=['single-code-file','whole-code-directory'])
def test_local_code_native_argv_masks_a_replaced_draft_entry_symlink(tmp_path,monkeypatch,directory):
    """The readonly code tree masks a mutable entry without reading its target."""
    from scidiscovery.research_loop import code_versions, native_execution, shared_input_views, experiment_client
    fixed=_fixed_local_code(tmp_path,monkeypatch,directory=directory)
    root,work,adapter,payload,version,export,source,cwd,options=fixed
    value,config,accepted,public=_local_code_launch_inputs(tmp_path,fixed)
    private=root/'private.txt';private.write_text('host private fixture')
    source.unlink();source.symlink_to(private)
    (source.parent/'new-draft-sibling.py').write_text("raise AssertionError('draft sibling')\n")
    materialized=[]
    def fixed_export(instance,**kwargs):
        materialized.append((instance,{key:item for key,item in kwargs.items() if key!='context'}))
        return export
    def forbidden(*args,**kwargs):
        pytest.fail('Fixed code argv must not read its mutable entry or start a process')
    monkeypatch.setattr(experiment_client,'_observe_input_reference',forbidden)
    for name in ('Popen','run','call'):monkeypatch.setattr(subprocess,name,forbidden)
    monkeypatch.setattr(shared_input_views,'ensure_capability',lambda **kwargs:'/argv-only/bwrap')
    monkeypatch.setattr(code_versions,'materialize',fixed_export)
    argv,host_cwd,finish=native_execution._launch(value,config,accepted,accepted['command'],accepted['cwd'],public=public,root=root)
    assert materialized==[(root,dict(step=1,call_id=payload.name,code_version=version))]
    bindings=[argv[index:index+3] for index,argument in enumerate(argv) if argument in ('--bind','--ro-bind')]
    code_bindings=[(index,row) for index,row in enumerate(bindings) if row[2]==str(work/'code')]
    last_code_index,last_code_binding=code_bindings[-1]
    assert last_code_binding==['--ro-bind',str(export),str(work/'code')]
    assert all(row[0]=='--ro-bind' for _,row in code_bindings)
    assert not any(Path(row[2]).is_relative_to(work/'code') for row in bindings[last_code_index+1:])
    assert not any(row[2]==str(source) for row in bindings)
    assert argv[-len(accepted['command']):]==[sys.executable,str(source),'explicit-argument']
    assert argv[argv.index('--chdir')+1]==str(cwd) and host_cwd is finish is None
    assert source.is_symlink() and private.read_text()=='host private fixture'
    assert (export/'run.py').read_text()=="print('fixed source')\n"
    assert not (export/'new-draft-sibling.py').exists()


@pytest.mark.parametrize('changed',['logical-source','working-directory','code-pin','scientific-mount','artifact-alias'])
def test_local_code_native_argv_refuses_changed_bindings_before_launch(tmp_path,monkeypatch,changed):
    from scidiscovery.research_loop import code_versions, native_execution, shared_input_views
    fixed=_fixed_local_code(tmp_path,monkeypatch)
    root,work,adapter,payload,version,export,source,cwd,options=fixed
    value,config,accepted,public=_local_code_launch_inputs(tmp_path,fixed)
    monkeypatch.setattr(shared_input_views,'ensure_capability',lambda **kwargs:'/usr/bin/bwrap')
    monkeypatch.setattr(code_versions,'materialize',lambda *args,**kwargs:export)
    baseline=native_execution._launch(value,config,accepted,accepted['command'],accepted['cwd'],public=public,root=root)
    assert str(export) in baseline[0]
    expected=e.IntegrityError
    def unavailable_export(*args,**kwargs):
        # The real mount owner discovers this alias after the fixed tree exists.
        if changed=='scientific-mount':return export
        if changed!='code-pin':pytest.fail('Invalid logical bindings must fail before code materialization')
        assert kwargs['code_version']==accepted['code_version']
        raise ValueError('Original code pin differs')
    monkeypatch.setattr(code_versions,'materialize',unavailable_export)
    if changed=='logical-source':
        accepted['source_script']=str(work/'code/other.py')
    elif changed=='working-directory':
        accepted['cwd']=str(work/'code');expected=e.UnsupportedExecution
    elif changed=='code-pin':
        accepted['code_version']=dict(version,commit='b'*40)
    elif changed=='scientific-mount':
        original=root/'frozen-data.txt';original.write_text('fixed scientific input')
        job=c.read(root/'jobs/001.json')
        job['global_materials'].append(dict(path='code/helper.py',source=str(original),size_bytes=original.stat().st_size))
        c.write(root/'jobs/001.json',job)
        expected=e.UnsupportedExecution
    elif changed=='artifact-alias':
        (cwd/'artifacts').rmdir();(cwd/'artifacts').symlink_to(work/'code');expected=e.UnsupportedExecution
    with pytest.raises(expected):
        native_execution._launch(value,config,accepted,accepted['command'],accepted['cwd'],public=public,root=root)
    assert (export/'run.py').read_text()=="print('fixed source')\n"
    assert all((cwd/name).is_dir() for name in ('.git','.agents','.codex','.aws'))
