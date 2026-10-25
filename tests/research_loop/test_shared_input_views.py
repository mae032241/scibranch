"""Readonly mounts exercise real local processes; no provider or solver runs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from scidiscovery.research_loop import contracts as c, shared_input_views as views
from scidiscovery.research_loop.experiments import UnsupportedExecution


@pytest.fixture
def layout(tmp_path, monkeypatch):
    root=tmp_path/'instance';work=root/'project/nodes/001'
    (work/'inputs').mkdir(parents=True);(work/'work').mkdir();(work/'artifacts').mkdir()
    source=work/'inputs/original.txt';source.write_text('shared original');source.chmod(0o400)
    alias=work/'artifacts/old-alias.txt';os.link(source,alias)
    canonical=root/'host-history/objects/content';canonical.parent.mkdir(parents=True);os.link(source,canonical)
    monkeypatch.setattr('scidiscovery.research_loop.skill_supply.native_mounts',lambda root,job:[])
    return root,work,source,alias,canonical


def require_mounts():
    try:views.ensure_capability()
    except UnsupportedExecution as error:pytest.skip(str(error))


def test_exact_aliases_and_dynamic_root_order(layout):
    root,work,source,alias,canonical=layout
    initial=views.workspace_arguments(root,work)
    final=views.mount_arguments(root,{},work,private=True)
    assert initial==['--bind',str(work),str(work),'--bind',str(work/'work'),str(work/'work')]
    triples=list(zip(final[::3],final[1::3],final[2::3]))
    assert ('--ro-bind',str(alias),str(alias)) in triples
    assert ('--ro-bind',str(canonical.parent),str(canonical.parent)) in triples
    assert all(('--ro-bind',str(work/'work'/name),str(work/'work'/name)) in triples for name in views.DYNAMIC_ROOTS)
    assert str(root) not in final


def test_dynamic_publisher_refuses_replaced_root_and_parent(layout):
    root,work,*_=layout
    retained=views.dynamic_root(root,work,'retained-experiments')
    retained.rename(retained.with_name('old-retained'));retained.mkdir()
    with pytest.raises(UnsupportedExecution,match='replaced'):
        views.dynamic_root(root,work,'retained-experiments')
    retained.rmdir();retained.with_name('old-retained').rename(retained)
    (work/'work').rename(work/'old-work');(work/'work').mkdir()
    with pytest.raises((UnsupportedExecution,FileNotFoundError)):
        views.dynamic_root(root,work,'execution-results')
    assert not (work/'work/execution-results').exists()


def test_missing_capability_never_returns_bare_argv(layout,monkeypatch):
    root,work,*_=layout
    monkeypatch.setattr(views.shutil,'which',lambda _:None)
    with pytest.raises(UnsupportedExecution,match='bwrap'):
        views.wrap(root,{},work,[sys.executable,'-c','raise SystemExit(99)'])


def test_metadata_capability_limit_is_independent_of_storage_quota(layout,monkeypatch):
    root,work,*_=layout
    monkeypatch.setattr(views,'ALIAS_MAX_ENTRIES',1)
    with pytest.raises(UnsupportedExecution,match='metadata capability limit'):
        views.mount_arguments(root,{},work)


def test_projection_mapping_keeps_private_roots_out(layout):
    root,work,*_=layout
    context=root/'host-recovery/context';context.mkdir(parents=True)
    (context/'inputs').mkdir();(context/'inputs/display.txt').write_text('science display')
    result=views.mount_arguments(root,{},work,target_work='/task',mounts=[(context/'inputs','/task/inputs')],include_skills=False)
    triples=list(zip(result[::3],result[1::3],result[2::3]))
    assert ('--ro-bind',str(context/'inputs'),'/task/inputs') in triples
    assert not any('host-history/objects' in value for value in result)
    assert not any(value=='/task' for value in result)


PROBE = r'''import json,os,pathlib,subprocess,sys,time
work=pathlib.Path(sys.argv[1]);external=pathlib.Path(sys.argv[2])
assert os.environ['SCID_PRESERVED_NATIVE_VALUE']=='unchanged'
assert sys.argv[3:]==['--network=original','--web=live','--subagents=on']
def denied(action):
 try:action()
 except OSError:pass
 else:raise AssertionError('Shared input mutation succeeded')
for path in (work/'inputs/original.txt',work/'artifacts/old-alias.txt',external):
 denied(lambda:path.chmod(0o600));denied(lambda:path.write_text('changed'))
 assert path.read_text()=='shared original'
for path in (work,work/'work',*[work/'work'/name for name in ('retained-experiments','execution-results','retained-implementations')]):
 denied(lambda:path.rename(path.with_name(path.name+'-moved')))
(work/'work/ready').write_text('ready')
deadline=time.monotonic()+10
retained=work/'work/retained-experiments/call/outputs/new.txt'
while not retained.exists():
 assert time.monotonic()<deadline
 time.sleep(.02)
assert retained.read_text()=='shared original'
denied(lambda:retained.chmod(0o600));denied(lambda:retained.write_text('changed'))
for name in json.loads(os.environ.get('SCID_TEST_ADDITIONAL_ALIASES','[]')):
 path=pathlib.Path(name);assert path.read_text()=='shared original'
 denied(lambda:path.chmod(0o600));denied(lambda:path.write_text('changed'))
child='import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.write_text("changed")'
assert subprocess.run([sys.executable,'-c',child,str(retained)],capture_output=True).returncode!=0
(work/'work/new.txt').write_text('ordinary work')
(work/'artifacts/new.txt').write_text('ordinary artifact')
print('kernel readonly checks completed',flush=True)
'''


@pytest.mark.process_e2e
def test_real_process_dynamic_publication_and_native_permissions(layout,monkeypatch):
    require_mounts()
    root,work,source,alias,canonical=layout
    monkeypatch.setenv('SCID_PRESERVED_NATIVE_VALUE','unchanged')
    c.write(root/'jobs/001.json',dict(step=1,role='W',worker_view_version=1))
    c.write(root/'jobs/002.json',dict(step=2,role='H'))
    previous=root/'project/nodes/002';(previous/'inputs').mkdir(parents=True)
    (previous/'work').mkdir();(previous/'artifacts').mkdir()
    prior_input=previous/'inputs/objective.md';os.link(source,prior_input)
    author=root/'host-recovery/author-context/002/001/author-science-context/inputs/input.txt'
    author.parent.mkdir(parents=True);os.link(source,author)
    from scidiscovery.research_loop import experiment_client
    records=experiment_client.record_directory(root,1)
    staged=records/'.prepared-live/source.py'
    monkeypatch.setenv('SCID_TEST_ADDITIONAL_ALIASES',json.dumps([str(prior_input),str(author),str(staged)]))
    argv=[sys.executable,'-I','-c',PROBE,str(work),str(canonical),
        '--network=original','--web=live','--subagents=on']
    command=views.wrap(root,{},work,argv)
    assert command[-len(argv):]==argv
    process=subprocess.Popen(command,cwd=work,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+10
        while not (work/'work/ready').exists() and process.poll() is None and time.monotonic()<deadline:
            time.sleep(.02)
        assert (work/'work/ready').exists(),process.communicate(timeout=2)
        staged.parent.mkdir();os.link(source,staged)
        target=views.dynamic_root(root,work,'retained-experiments')/'call/outputs/new.txt'
        target.parent.mkdir(parents=True);os.link(source,target)
        stdout,stderr=process.communicate(timeout=15)
        assert process.returncode==0,(stdout,stderr)
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=2)
    assert source.read_text()==alias.read_text()==canonical.read_text()=='shared original'
    assert source.stat().st_mode&0o777==0o400
    assert (work/'work/new.txt').read_text()=='ordinary work'
    assert (work/'artifacts/new.txt').read_text()=='ordinary artifact'

NATIVE_PROBE = r'''import json,os,pathlib,subprocess,sys
backend=sys.argv[1];work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);request=sys.stdin.read()
args=sys.argv[2:]
if backend=='codex':
 assert args[args.index('--sandbox')+1]=='danger-full-access'
 assert 'features.multi_agent=true' in args and 'web_search="live"' in args
else:
 envelope=json.loads(request)
 assert envelope['permissions']['declaration']['workspace']=='danger-full-access'
 assert envelope['permissions']['declaration']['subagents'] is True
for path in (work/'inputs/global/objective.md',work/'artifacts/input-alias'):
 for action in (lambda:path.chmod(0o600),lambda:path.write_text('changed')):
  try:action()
  except OSError:pass
  else:raise AssertionError('Native final Popen did not protect shared input')
 child='import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("changed")'
 assert subprocess.run([sys.executable,'-c',child,str(path)],capture_output=True).returncode!=0
(work/'artifacts/native-write').write_text('native writable output')
(work/'work/native-args.json').write_text(json.dumps(dict(argv=args,request=request)))
task=json.loads((work/'science-task.json').read_text())
payload={key:None for key in task['final_response']}
payload.update(summary='Inert readonly process test',scientific_result='Kernel denied shared input mutation.',
 limitations=['No model or solver was invoked.'],implementation_selection=None,
 next_action='wait',route_reason='Bounded transport test complete')
if backend=='codex':
 pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
 print(json.dumps(dict(type='thread.started',thread_id='readonly-fixture')),flush=True)
 print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=1,cached_input_tokens=0,output_tokens=1))),flush=True)
else:
 print(json.dumps(dict(type='session',session_id='readonly-protocol-fixture')),flush=True)
 print(json.dumps(dict(type='result',result=payload)),flush=True)
 print(json.dumps(dict(type='completed',usage=dict(input_tokens=1,cached_input_tokens=0,output_tokens=1))),flush=True)
'''


@pytest.mark.process_e2e
@pytest.mark.parametrize('backend',['codex','protocol'])
def test_final_native_popen_enforces_custom_and_protocol_without_registration(tmp_path,backend):
    require_mounts()
    from scidiscovery.research_loop import budget, controller, execution, native
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md'
    objective.write_text('Inert readonly native process validation.')
    launcher=tmp_path/'native-probe.py';launcher.write_text(NATIVE_PROBE)
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable,str(launcher),backend],
        max_tokens=1000,max_seconds=45,max_steps=2,model='no-model',reasoning_effort='high',
        search_policy=None,experiment_mode='external',native_backend=backend,
        native_options=dict(workspace='danger-full-access',network=False,web='live',subagents=True))
    c.write(root/'host-started.json',budget.clock_stamp());budget.transition(root,'advance','Readonly transport test')
    c.build_job(root,1,'H',[]);work=c.workspace(root,1)
    (work/'artifacts').mkdir(exist_ok=True)
    os.link(work/'inputs/global/objective.md',work/'artifacts/input-alias')
    native.execute_native(root,1)
    outcome=c.read(execution.origin(root,1)/'native-outcome.json')
    assert outcome['native_started'] and outcome['shared_inputs']['enforcement']=='kernel_readonly_mounts'
    assert outcome['native_permissions']['enforcement']['workspace']=='hint_only'
    assert (work/'artifacts/native-write').read_text()=='native writable output'
    assert (work/'inputs/global/objective.md').read_text()==objective.read_text()

@pytest.mark.parametrize('publisher',['collect','wake'])
def test_host_publishers_refuse_same_name_replacement_before_any_publish(layout,monkeypatch,publisher):
    root,work,*_=layout
    name='retained-experiments' if publisher=='collect' else 'execution-results'
    retained=views.dynamic_root(root,work,name)
    retained.rename(retained.with_name('old-'+name));retained.mkdir()
    if publisher=='collect':
        from scidiscovery.research_loop import experiment_client, experiments
        monkeypatch.setattr(experiment_client,'root_for_configuration',lambda config:root)
        monkeypatch.setattr(experiments,'scientific_outputs',lambda *a,**kw:pytest.fail('must refuse before publication'))
        with pytest.raises(UnsupportedExecution,match='replaced'):
            experiments._collected_view(dict(scope=dict(step=1)),'original-call',{},None)
    else:
        from scidiscovery.research_loop import managed_wait
        with pytest.raises(UnsupportedExecution,match='replaced'):
            managed_wait.wake_packet(root,dict(step=1),{})
    assert list(retained.iterdir())==[]


def test_legacy_public_compile_refuses_before_builder_or_shared_publication(layout,monkeypatch):
    from types import SimpleNamespace
    from scidiscovery.research_loop import execution_authority, execution_inputs
    root,work,*_=layout
    views.dynamic_root(root,work,'retained-implementations')
    monkeypatch.setattr(execution_authority,'_remaining',lambda *a:(None,5))
    monkeypatch.setattr(c,'settings',lambda *a:dict(storage_policy=None))
    host=SimpleNamespace(root=root,job=dict(step=1),config={})
    output=work/'artifacts/package'
    with pytest.raises(UnsupportedExecution,match='Legacy public compile'):
        execution_inputs.NativeInputHost.compile(host,work/'work/author.json',output,slots=[],aliases={},
            capability={},contract={},builder=lambda *a:pytest.fail('must reject before compilation'))
    assert not output.exists()


@pytest.mark.parametrize('shared_remaining',[0,-5,.000001])
def test_readonly_preparation_does_not_turn_accepted_soft_targets_into_a_deadline(monkeypatch,shared_remaining):
    from scidiscovery.research_loop import native
    monkeypatch.setattr(native.control_budget,'_node_duration',lambda *a:pytest.fail('ordinary node targets are soft'))
    context=native._shared_view_context(Path('/unused'),{},dict(continuation_kind='fragment'),
        dict(seconds_remaining=shared_remaining))
    assert 20<context.remaining_seconds()<=views.ALIAS_SECONDS+10


@pytest.mark.parametrize('kind,elapsed,expected',[
    ('final',8,4),('author',103,4),('author',108,None)])
def test_readonly_preparation_preserves_exact_existing_hard_clock(monkeypatch,kind,elapsed,expected):
    from scidiscovery.research_loop import native
    monkeypatch.setattr(native.control_budget,'_node_duration',lambda *a:elapsed)
    monkeypatch.setattr(native.control_budget,'node_limits',lambda *a:dict(max_seconds=12))
    kwargs=dict(final_return={} if kind=='final' else None,
        author_permit=dict(max_seconds=7) if kind=='author' else None)
    if expected is None:
        with pytest.raises(TimeoutError,match='Original native hard deadline'):
            native._shared_view_context(Path('/unused'),{},dict(budget_slice=dict(previous_seconds=100)),
                dict(seconds_remaining=-9),**kwargs)
    else:
        context=native._shared_view_context(Path('/unused'),{},dict(budget_slice=dict(previous_seconds=100)),
            dict(seconds_remaining=-9),**kwargs)
        assert expected-.5<context.remaining_seconds()<=expected


@pytest.fixture
def registered_namespace(monkeypatch):
    from scidiscovery.operations.execution import ExecutionInterfaceRegistration
    from scidiscovery.operations.runtime_plugins import RuntimePluginFactory
    from scidiscovery.research_loop import skill_supply
    registrations=[ExecutionInterfaceRegistration('namespace_fixture','1',RuntimePluginFactory(lambda _:None),
        native_launcher_module='namespace_fixture.launcher')]
    identity=dict(path='/fixture/original-module.py')
    class Entry:
        name='namespace_fixture'
        value='namespace_fixture:REGISTRATION'
        def load(self):return registrations[0]
    class Entries:
        def select(self,**filters):
            return [Entry()] if registrations and filters.get('name','namespace_fixture')=='namespace_fixture' else []
    monkeypatch.setattr(skill_supply.importlib.metadata,'entry_points',lambda:Entries())
    monkeypatch.setattr(skill_supply,'_module_identity',lambda module:dict(name=module,path='/private/provider.py',**identity))
    return registrations,identity


def test_registered_final_namespace_identity_is_frozen_without_any_skill_selection(registered_namespace):
    from scidiscovery.research_loop import backends,skill_supply
    argv=['python','-B','-I','-m','namespace_fixture.launcher','--config','/private/launcher.json','--']
    settings=backends.configuration('codex',argv,{})
    contract=settings['native_capabilities']['native_launcher_contract']
    assert contract['provider']['plugin_version']=='1' and 'skills' not in settings
    assert skill_supply.namespace_launcher(settings)==contract
    assert backends.configuration('codex',argv,{},existing=settings)['native_capabilities']==settings['native_capabilities']


@pytest.mark.parametrize('damage',['version','module','missing'])
def test_registered_final_namespace_changes_fail_closed_on_dispatch_and_configuration_update(registered_namespace,damage):
    from scidiscovery.operations.execution import ExecutionInterfaceRegistration
    from scidiscovery.operations.runtime_plugins import RuntimePluginFactory
    from scidiscovery.research_loop import backends,skill_supply
    registrations,identity=registered_namespace
    argv=['python','-m','namespace_fixture.launcher','--config','/private/launcher.json']
    settings=backends.configuration('codex',argv,{})
    if damage=='version':
        registrations[0]=ExecutionInterfaceRegistration('namespace_fixture','2',RuntimePluginFactory(lambda _:None),
            native_launcher_module='namespace_fixture.launcher')
    elif damage=='module':identity['path']='/fixture/different-module.py'
    else:registrations.clear()
    with pytest.raises(ValueError,match='changed'):
        skill_supply.namespace_launcher(settings)
    with pytest.raises(ValueError,match='changed'):
        backends.configuration('codex',argv,{},existing=settings)


@pytest.mark.parametrize('argv',[
    ['python','-c','opaque local wrapper'],
    ['python','-m','namespace_fixture.launcher','--config','/private/launcher.json','--','suffix'],
    ['arbitrary-local-command','--custom-policy']])
def test_opaque_local_argv_remains_on_common_wrapper_without_registration_gate(registered_namespace,argv):
    from scidiscovery.research_loop import backends,skill_supply
    settings=backends.configuration('codex',argv,{})
    assert skill_supply.namespace_launcher(settings) is None



def test_readonly_record_inventory_accepts_directory_only_without_creating_pins(layout):
    from scidiscovery.research_loop import experiment_client,experiments
    root,work,*_=layout
    c.write(root/'jobs/001.json',dict(step=1,role='W',worker_view_version=1))
    directory=experiment_client.record_directory(root,1)
    call=directory/'calls/original-call';call.mkdir(parents=True)
    records=experiments.Records(dict(record_dir=str(directory)))
    assert records.calls()==[call]
    assert not views._binding(root,work).exists()


def test_complete_record_publisher_refuses_replaced_holder_before_receipt_write(layout):
    from scidiscovery.research_loop import experiment_client,experiments
    root,work,*_=layout
    c.write(root/'jobs/001.json',dict(step=1,role='W',worker_view_version=1))
    directory=experiment_client.record_directory(root,1);directory.mkdir(parents=True)
    views.dynamic_root(root,work,'retained-experiments')
    views._pin_holders(root,work,[directory])
    directory.rename(directory.with_name('old-records'));directory.mkdir()
    records=experiments.Records(dict(record_dir=str(directory),scope=dict(step=1)))
    assert records.calls()==[]
    with pytest.raises(UnsupportedExecution,match='private original holder was replaced'):
        records.publish('original-call','terminal.json',{})
    assert list(directory.iterdir())==[]



def test_adapter_record_effect_refuses_replaced_holder_before_lock_or_adapter_write(layout):
    from scidiscovery.research_loop import experiment_client,experiments
    root,work,*_=layout
    c.write(root/'jobs/001.json',dict(step=1,role='W',worker_view_version=1))
    directory=experiment_client.record_directory(root,1);directory.mkdir(parents=True)
    views.dynamic_root(root,work,'retained-experiments')
    views._pin_holders(root,work,[directory])
    directory.rename(directory.with_name('old-records'));directory.mkdir()
    class Adapter:
        config=dict(record_dir=str(directory),scope=dict(step=1))
        records=experiments.Records(config)
        @experiments.record_effect
        def status_details(self):
            (self.records.root/'adapter-write').write_text('must not publish into a replaced holder')
    with pytest.raises(UnsupportedExecution,match='private original holder was replaced'):
        Adapter().status_details()
    assert not (directory/'record-writer.lock').exists()
    assert list(directory.iterdir())==[]


def _private_git_wrapper(tmp_path,monkeypatch,*,existing=False):
    """Real wrapper/private-Git delta over an inert existing data-view seam."""
    root=tmp_path/'ancestor/instance';work=root/'project/nodes/001'
    (work/'work').mkdir(parents=True);(work/'inputs').mkdir();(work/'artifacts').mkdir()
    holder=root/'host-runtime/code-versions'
    if existing:
        holder.mkdir(parents=True);holder.parent.chmod(0o751);holder.chmod(0o750)
    def forbidden(*args,**kwargs):pytest.fail('A wrapper argv test reached a process or capability probe')
    monkeypatch.setattr(views,'ensure_capability',lambda **kwargs:'/argv-only/bwrap')
    for name in ('Popen','run','call'):monkeypatch.setattr(subprocess,name,forbidden)
    data=['--ro-bind',str(work/'inputs'),str(work/'inputs')]
    monkeypatch.setattr(views,'mount_arguments',lambda *args,**kwargs:list(data))
    return root,work,holder,data


def test_git_wrapper_prepares_holder_before_existing_mounts_and_covers_later_children(tmp_path,monkeypatch):
    root,work,holder,data=_private_git_wrapper(tmp_path,monkeypatch)
    workspace=views.workspace_arguments;events=[]
    def prepared(*args,**kwargs):
        assert holder.is_dir() and holder.stat().st_mode&0o777==0o700
        assert not (holder/'repository.git').exists() and not (holder/'exports').exists()
        events.append(holder.stat().st_ino)
        return workspace(*args,**kwargs)
    monkeypatch.setattr(views,'workspace_arguments',prepared)
    argv=['custom-native','--sandbox','danger-full-access','--network=original','--web=live','--subagents=on']
    command=views.wrap(root,{},work,argv);divider=command.index('--')
    assert command[divider+1:]==argv and events==[holder.stat().st_ino]
    assert command[divider-3:divider]==['--ro-bind',str(holder),str(holder)]
    existing=[*workspace(root,work),*data]
    assert command[divider-3-len(existing):divider-3]==existing
    for relative in ('repository.git/objects/blob','capture-later.index','exports/001/call/tree/code.py'):
        child=holder/relative;child.parent.mkdir(parents=True,exist_ok=True);child.write_text('later host child')
        assert str(child) not in command[:divider]
    assert holder.stat().st_ino==events[0]


def test_git_wrapper_pins_ordinary_ancestors_in_order_and_keeps_modes(tmp_path,monkeypatch):
    root,work,holder,data=_private_git_wrapper(tmp_path,monkeypatch,existing=True)
    paths=(root,holder.parent,holder,work,work/'work',work/'artifacts')
    modes={path:path.stat().st_mode for path in paths}
    command=views.wrap(root,{},work,['native','--permission=original'])
    assert command[:9]==['/argv-only/bwrap','--unshare-user','--unshare-pid','--die-with-parent',
        '--bind','/','/','--proc','/proc']
    first_workspace=command.index(str(work))-1
    ancestors=command[9:first_workspace]
    triples=[tuple(ancestors[index:index+3]) for index in range(0,len(ancestors),3)]
    destinations=[Path(target) for mode,source,target in triples if mode=='--bind' and source==target]
    assert len(destinations)==len(triples)==len(holder.parents)-1
    assert destinations[0].parent==Path('/') and destinations[-3:]==[root.parent,root,holder.parent]
    assert all(child.parent==parent for parent,child in zip(destinations,destinations[1:]))
    assert Path('/') not in destinations and holder not in destinations
    divider=command.index('--')
    assert command[divider-3:divider]==['--ro-bind',str(holder),str(holder)]
    assert {path:path.stat().st_mode for path in paths}==modes


@pytest.mark.parametrize('part,kind',[
    ('root','file'),('root','symlink'),('root','missing'),
    ('runtime','file'),('runtime','symlink'),('holder','file'),('holder','symlink')])
def test_git_wrapper_refuses_nonordinary_holder_or_ancestors_before_data_mounts(tmp_path,monkeypatch,part,kind):
    valid,work,holder,_=_private_git_wrapper(tmp_path/'valid',monkeypatch)
    assert str(holder) in views.wrap(valid,{},work,['native'])
    root=tmp_path/'broken/instance';target={'root':root,'runtime':root/'host-runtime',
        'holder':root/'host-runtime/code-versions'}[part]
    target.parent.mkdir(parents=True,exist_ok=True)
    if kind=='file':target.write_text('ordinary fixture file')
    elif kind=='symlink':
        ordinary=tmp_path/'ordinary';ordinary.mkdir();target.symlink_to(ordinary,target_is_directory=True)
    def unexpected(*args,**kwargs):pytest.fail('Invalid private Git paths reached the existing data-view seam')
    monkeypatch.setattr(views,'workspace_arguments',unexpected)
    monkeypatch.setattr(views,'mount_arguments',unexpected)
    with pytest.raises(UnsupportedExecution):views.wrap(root,{},root/'project/nodes/001',['native'])
    if kind=='file':assert target.read_text()=='ordinary fixture file'
    elif kind=='symlink':assert target.is_symlink() and not (ordinary/'code-versions').exists()
