"""Cov single-source admission and public contracts; no executor is started."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import experiments as e
from cov_execution import experiments as cov_experiments
from test_cov_lineage import wrapper
from execution_fixtures import execution_registry, remote_registry


@pytest.mark.parametrize('options', ['directory', 'arguments'])
def test_cov_unsupported_options_are_known_not_started_before_prepare(wrapper, monkeypatch, capsys, options):
    script = wrapper.script('candidate')

    def forbidden(*args, **kwargs):
        pytest.fail('Unsupported caller options reached executor preparation or submission')

    monkeypatch.setattr(cov_experiments.CovExecution, 'prepare', forbidden)
    monkeypatch.setattr(cov_experiments.CovExecution, 'submit', forbidden)
    args = ['--config', str(wrapper.config), 'run', '--script', str(script)]
    args += ['--experiment-dir', str(wrapper.root)] if options == 'directory' else ['--', '--seed', '7']
    assert e.main(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'known_not_started' and result['accepted'] is False
    assert result['error'] == 'caller_error' and '--experiment-dir or script arguments' in result['note']
    assert 'call_id' not in result and wrapper.accepted() == [] and wrapper.calls() == b''


def test_other_admission_errors_keep_unresolved_classification(wrapper, capsys):
    script = wrapper.script('candidate')
    assert e.main(['--config', str(wrapper.config), 'run', '--script', str(script),
        '--parent-plan', 'absent-candidate']) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'unresolved' and result['note'] == 'parent_plan_unresolved'
    assert 'accepted' not in result and wrapper.accepted() == [] and wrapper.calls() == b''


def test_cov_single_source_contract_is_frozen_discoverable_and_exact(wrapper, capsys):
    original_config=wrapper.config.read_bytes()
    config = json.loads(wrapper.config.read_text())
    frozen = config['execution_interface']['public_capabilities']
    assert e.INPUT_SOURCES_KEY not in {item['key'] for item in frozen}
    assert e.main(['--config', str(wrapper.config), 'capabilities']) == 0
    listed = json.loads(capsys.readouterr().out)['contracts']
    scientific_fields={'key','kind','schema_id','payload_schema_version','media_type','public_summary'}
    assert all(set(item)==scientific_fields for item in listed)
    assert all(set(item)==scientific_fields|{'size_bytes'} for item in frozen)
    assert listed == [{key:item[key] for key in scientific_fields} for item in frozen]
    summary = next(item['public_summary'] for item in listed if item['key'] == 'cov_single_source')
    assert summary['accepted_source'] == 'exact source.py snapshot'
    assert summary['experiment_directory'] is False and summary['script_arguments'] is False
    output = wrapper.root / 'single-source-contract.json'
    assert e.main(['--config', str(wrapper.config), 'describe', '--key', 'cov_single_source', '--output', str(output)]) == 0
    capsys.readouterr()
    assert json.loads(output.read_text()) == summary
    bound=next(item for item in frozen if item['key']=='cov_single_source')
    assert output.stat().st_size==bound['size_bytes']
    changed = deepcopy(config)
    changed['execution_interface']['public_capabilities'] = []
    with pytest.raises(e.IntegrityError, match='contract or configuration changed'):
        e.load(changed, path=wrapper.config)
    for field,value in (('public_summary',dict(unapproved=True)),('size_bytes',bound['size_bytes']+1)):
        changed=deepcopy(config)
        next(item for item in changed['execution_interface']['public_capabilities'] if item['key']=='cov_single_source')[field]=value
        with pytest.raises(e.IntegrityError,match='contract or configuration changed'):
            e.load(changed,path=wrapper.config)
    assert wrapper.config.read_bytes()==original_config


@pytest.mark.parametrize('registered', [False, True])
def test_public_contracts_freeze_without_managed_source_key(tmp_path, remote_registry, registered):
    from scidiscovery.research_loop.protocol import CURRENT
    options = dict(registered_implementation=dict(factory='previous-installation:factory',plugin_version='old',code=[])) if registered else {}
    config = e.freeze(dict(options, record_dir=str(tmp_path / 'records'),
        scope=dict(handoff_protocol=CURRENT, step=2, role='W', job_ref=dict(path='/fixture/jobs/002.json'),
            execution_key=dict(path='/fixture/native-evidence/002/native-intent.json'), mechanism_ref=None)), 'remote_execution:fixture', path=tmp_path / 'config.json')
    assert ('registered_implementation' in config) is registered
    contracts = config['execution_interface']['public_capabilities']
    assert [item['key'] for item in contracts] == ['opaque_input']
    assert contracts == e._public_capabilities(e.load(config, path=tmp_path / 'config.json'))
    changed = deepcopy(config)
    changed['execution_interface']['public_capabilities'][0]['size_bytes'] += 1
    with pytest.raises(e.IntegrityError, match='contract or configuration changed'):
        e.load(changed, path=tmp_path / 'config.json')


@pytest.mark.parametrize('registered', [False, True])
def test_empty_capability_adapter_preserves_public_contract_presence(tmp_path, remote_registry, monkeypatch, registered):
    from execution_fixtures import DifferentExternalIdentity
    from scidiscovery.research_loop.protocol import CURRENT
    monkeypatch.setattr(DifferentExternalIdentity, 'capabilities', lambda self: ())
    options = dict(registered_implementation=dict(factory='previous-installation:factory',plugin_version='old',code=[])) if registered else {}
    config = e.freeze(dict(options, record_dir=str(tmp_path / 'records'),
        scope=dict(handoff_protocol=CURRENT, step=2, role='W', job_ref=dict(path='/fixture/jobs/002.json'),
            execution_key=dict(path='/fixture/native-evidence/002/native-intent.json'), mechanism_ref=None)), 'remote_execution:fixture', path=tmp_path / 'config.json')
    interface = config['execution_interface']
    assert ('public_capabilities' in interface) is registered
    if registered:
        assert interface['public_capabilities'] == []


# Pure G1 source/argv seams. All native, bwrap, Git and process entries are inert.
def _fixed_cov_launcher(tmp_path,monkeypatch,*,local=False,directory=False):
    """Source/argv only: every namespace, Git and process entry is inert."""
    from cov_execution import launcher, receipt
    from scidiscovery.research_loop import code_versions, contracts as c, shared_input_views
    root=tmp_path/'instance';root.mkdir(parents=True)
    work=c.workspace(root,1);work.mkdir(parents=True)
    c.write(root/'jobs/001.json',dict(step=1,role='W',worker_view_version=1,
        global_materials=[],direct_refs=[],direct_materials=[]))
    code=work/'code';code.mkdir()
    source=code/'run.py';source.write_text("print('fixed source')\n")
    (code/'new-draft-sibling.py').write_text("raise AssertionError('draft sibling')\n")
    call_id='0001-source-argv'
    version=dict(version=1,object_format='sha1',commit='a'*40,tree_path='.',entry='run.py',
        selection='directory' if directory else 'file',logical_root='code')
    historical=root/'historical-export/run.py';historical.parent.mkdir()
    historical.write_bytes(source.read_bytes());script=e.proof(historical)
    export=root/'host-runtime/code-versions/exports/001'/call_id/'tree';export.mkdir(parents=True)
    (export/'run.py').write_bytes(source.read_bytes())
    if directory:
        (export/'helper.py').write_text("VALUE='fixed helper'\n")
        (export/'resource.txt').write_text('fixed resource')
    public=tmp_path/'public';harness=public/'environment/harness';harness.mkdir(parents=True)
    runner=harness/'runner.py'
    runner.write_text("def main():\n with open(RESULTS_LOG, 'a') as f:\n  f.write(json.dumps(rec) + '\\n')\n print(json.dumps(rec, indent=2))\n sys.exit(0 if rec.get('ok') else 1)\n")
    config=dict(run_argv=[sys.executable],max_seconds=30,scope=dict(step=1,role='W'),
        record_dir=str(root/'host-runtime/experiment-control/001/records'),
        execution_interface=dict(executor='local_execution:command' if local else 'cov_execution:experiment'))
    cwd=work/'experiments/sentinel' if local else work;cwd.mkdir(parents=True,exist_ok=True)
    command=[sys.executable,str(source),'explicit-argument'] if local else [sys.executable,'run','--script',str(source)]
    accepted=dict(call_id=call_id,scope=config['scope'],parent=dict(status='unknown',candidate_id=None,source='none'),
        code_version=version,script=script,source_script=str(source),snapshot_files=[],command=command,
        script_index=1 if local else 3,cwd=str(cwd) if local else None,
        prepared=dict(local_path=str(historical),size_bytes=script['size_bytes']),
        receipt_transport=dict(version=1,channel='public_runner_main',runner=receipt.runner_profile(runner)))
    if local:accepted['experiment_directory']=str(cwd)
    record=Path(config['record_dir'])/'calls'/call_id;c.write(record/'accepted.json',accepted)
    historical.unlink()  # Historical export inode is deliberately unavailable.
    home=tmp_path/'native-home';home.mkdir();(home/'config.toml').write_text('')
    helper=root/'host-runtime/native-bin/codex-linux-sandbox';helper.parent.mkdir(parents=True)
    helper.write_text('Inert argv fixture; never executed.\n')
    settings=dict(scene_root=str(root),public_root=str(public),runs_dir=str(root/'runs'),
        submission_dir=str(root/'submission'),native=str(helper),code_mode_host=dict(path=str(helper)),
        runtime_paths=[],task_python=sys.executable,profile='fixture',_worker_science_view=True)
    materialized=[];verified=[];frozen_version=deepcopy(version)
    def materialize(instance,**kwargs):
        materialized.append((instance,{key:value for key,value in kwargs.items() if key!='context'}))
        if kwargs['code_version']!=frozen_version:raise ValueError('Original code pin differs')
        return export
    def verify(instance,**kwargs):
        verified.append((instance,{key:value for key,value in kwargs.items() if key!='context'}))
        if kwargs['code_version']!=frozen_version:raise ValueError('Original code pin differs')
    def forbidden_process(*args,**kwargs):
        pytest.fail('A source/argv fixture must never start a process')
    for name in ('Popen','run','call'):monkeypatch.setattr(receipt.subprocess,name,forbidden_process)
    monkeypatch.setattr(code_versions,'materialize',materialize)
    monkeypatch.setattr(code_versions,'verify',verify)
    monkeypatch.setattr(shared_input_views,'ensure_capability',lambda **kwargs:'/argv-only/bwrap')
    monkeypatch.setattr(launcher,'owner_metadata',lambda *args:[])
    # Existing HEAD data alias inventory has separate bounded fixtures. This
    # seam isolates G1 code-directory mount composition only.
    monkeypatch.setattr(shared_input_views,'mount_arguments',lambda *args,**kwargs:[])
    materials=[]
    return SimpleNamespace(root=root,work=work,source=source,version=version,export=export,config=config,
        accepted=accepted,record=record,cwd=cwd,settings=settings,materials=materials,
        materialized=materialized,verified=verified,
        launch=launcher.backend_launcher(settings,work,home,materials))


def _cov_argv_bindings(argv):
    return [argv[index:index+3] for index,value in enumerate(argv) if value in ('--bind','--ro-bind')]


def _fixed_cov_harness_roles(case):
    """Transport remains HEAD2; only the four runtime replay roles carry proof3."""
    from scidiscovery.research_loop import contracts as c
    harness=Path(case.settings['public_root'])/'environment/harness';items=[]
    for name in ('train.csv','val.csv','meta.json'):
        path=harness/name;path.write_text('frozen '+name)
        items.append(dict(e.proof(path),name='replay/harness/'+name))
    protocol=case.root/'replay-protocol.py';protocol.write_text('inert protocol original\n')
    items.append(dict(e.proof(protocol),name='replay/scid-current-protocol'))
    case.accepted['replay_runtime']=dict(e.proof(Path(sys.executable).resolve()),
        invoked_path=str(Path(sys.executable).absolute()),inputs=items)
    runner=case.accepted['receipt_transport']['runner']
    assert 'file_stat' not in runner
    assert set(runner)>={'path','size_bytes'}
    assert all(set(item)=={'path','size_bytes','file_stat','name'} for item in items)
    c.write(case.record/'accepted.json',case.accepted)
    return items,protocol


def _confirm_cov_argv(case):
    argv,host_cwd,finish=case.launch(case.config,case.accepted,case.accepted['command'],case.accepted['cwd'])
    if finish is not None:
        row=dict(script=str(case.source),run_id=1,ok=True)
        (case.record/'backend-science/outputs/row.json').write_text(json.dumps(row));finish(0,True,True)
    assert str(case.export) in argv and host_cwd is None
    return argv


@pytest.mark.parametrize('draft',['missing','changed','symlink'])
def test_cov_code_command_and_backend_argv_ignore_draft_and_historical_export(tmp_path,monkeypatch,draft):
    from scidiscovery.research_loop import experiment_client
    from cov_execution import receipt
    case=_fixed_cov_launcher(tmp_path,monkeypatch)
    case.source.unlink()
    if draft=='changed':case.source.write_text("raise AssertionError('mutable draft')\n")
    elif draft=='symlink':
        private=case.root/'private.txt';private.write_text('host private fixture')
        case.source.symlink_to(private)
    def no_draft_reference(*args,**kwargs):
        pytest.fail('Fixed code must not consult the current logical source')
    monkeypatch.setattr(experiment_client,'input_reference',no_draft_reference)
    adapter=object.__new__(cov_experiments.CovExecution)
    adapter.config=case.config;adapter.records=e.Records(case.config)
    assert adapter.command(case.accepted)[-1]==str(case.record/'accepted.json')
    argv,host_cwd,finish=case.launch(case.config,case.accepted,case.accepted['command'],case.accepted['cwd'])
    expected=(case.root,dict(step=1,call_id=case.accepted['call_id'],code_version=case.version))
    assert case.materialized==[expected] and host_cwd is None
    bindings=_cov_argv_bindings(argv)
    assert [row for row in bindings if row[2]==str(case.work/'code')]==[
        ['--ro-bind',str(case.export),str(case.work/'code')]]
    assert not any(row[2]==str(case.source) for row in bindings)
    for source,target in ((case.settings['runs_dir'],'/opt/exp/runs'),
            (case.settings['submission_dir'],'/submission')):
        assert ['--bind',source,target] in bindings
        assert ['--bind',source,source] in bindings
    assert argv[argv.index('--chdir')+1]==str(case.work)
    assert argv[-2:]==['--science-runner','/opt/exp/scid-invocation.json']
    invocation=json.loads((case.record/'backend-science/inputs/invocation.json').read_text())
    assert invocation==dict(runner=case.accepted['receipt_transport']['runner']['path'],
        source=str(case.source),output_line=case.accepted['receipt_transport']['runner']['output_line'],
        output='/task-tmp/scid-receipt/row.json')
    row=dict(script=str(case.source),run_id=1,ok=True,wall_seconds=.01)
    (case.record/'backend-science/outputs/row.json').write_text(json.dumps(row))
    finish(0,True,True)
    assert case.verified==[expected]
    assert json.loads((case.record/'receipt.json').read_text())==receipt.envelope(case.accepted,row)
    assert (case.export/'run.py').read_text()=="print('fixed source')\n"
    assert not (case.export/'new-draft-sibling.py').exists()


@pytest.mark.parametrize('changed',['prepared-path','prepared-size','logical-source','code-version','legacy-entry'])
def test_cov_code_command_refuses_changed_descriptor_or_host_binding(tmp_path,monkeypatch,changed):
    case=_fixed_cov_launcher(tmp_path,monkeypatch)
    adapter=object.__new__(cov_experiments.CovExecution)
    adapter.config=case.config;adapter.records=e.Records(case.config)
    assert adapter.command(case.accepted)[-1]==str(case.record/'accepted.json')
    if changed=='prepared-path':case.accepted['prepared']['local_path']+='-other'
    elif changed=='prepared-size':case.accepted['prepared']['size_bytes']+=1
    elif changed=='logical-source':case.accepted['source_script']=str(case.work/'code/other.py')
    elif changed=='code-version':case.accepted['code_version']['commit']='invalid'
    else:case.accepted['entry']='run.py'
    adapter=object.__new__(cov_experiments.CovExecution)
    adapter.config=case.config;adapter.records=e.Records(case.config)
    with pytest.raises(e.IntegrityError):adapter.command(case.accepted)
    assert case.materialized==case.verified==[]


@pytest.mark.parametrize('changed',['code-pin','logical-source','code-root-link','input-child','material-root','material-parent'])
def test_cov_code_backend_argv_refuses_changed_pin_namespace_and_mounts(tmp_path,monkeypatch,changed):
    valid=_fixed_cov_launcher(tmp_path/'valid',monkeypatch)
    _confirm_cov_argv(valid)
    from scidiscovery.research_loop import contracts as c
    case=_fixed_cov_launcher(tmp_path/'broken',monkeypatch);expected=e.IntegrityError
    if changed=='code-pin':case.accepted['code_version']['commit']='b'*40
    elif changed=='logical-source':case.accepted['source_script']=str(case.work/'code/other.py')
    elif changed=='code-root-link':
        (case.work/'code').rename(case.work/'moved-code');(case.work/'code').symlink_to(case.work/'moved-code')
        expected=e.UnsupportedExecution
    else:
        original=case.root/'fixed-input.txt';original.write_text('frozen scientific input')
        if changed=='input-child':
            from scidiscovery.research_loop import shared_input_views
            monkeypatch.setattr(shared_input_views,'mount_arguments',lambda *args,**kwargs:
                ['--ro-bind',str(original),str(case.work/'code/data.txt')])
        else:
            case.materials.append((str(original),str(case.work/'code') if changed=='material-root' else str(case.work)))
        expected=e.UnsupportedExecution
    c.write(case.record/'accepted.json',case.accepted)
    with pytest.raises(expected):
        case.launch(case.config,case.accepted,case.accepted['command'],case.accepted['cwd'])
    if changed in {'logical-source','code-root-link'}:assert case.materialized==[]
    assert case.verified==[] and not (case.record/'receipt.json').exists()
    assert (case.export/'run.py').read_text()=="print('fixed source')\n"
    assert not (case.export/'data.txt').exists()


@pytest.mark.parametrize('changed',['code-pin','row-source'])
def test_cov_code_backend_finish_requires_original_pin_and_logical_row(tmp_path,monkeypatch,changed):
    valid=_fixed_cov_launcher(tmp_path/'valid',monkeypatch)
    _confirm_cov_argv(valid)
    case=_fixed_cov_launcher(tmp_path/'broken',monkeypatch)
    _,_,finish=case.launch(case.config,case.accepted,case.accepted['command'],case.accepted['cwd'])
    row=dict(script=str(case.source),run_id=1,ok=True)
    if changed=='code-pin':case.version['commit']='b'*40
    else:row['script']=str(case.export/'run.py')
    (case.record/'backend-science/outputs/row.json').write_text(json.dumps(row))
    with pytest.raises(e.IntegrityError):finish(0,True,True)
    assert len(case.verified)==1 and not (case.record/'receipt.json').exists()


@pytest.mark.parametrize('directory',[False,True],ids=['single-code-file','whole-code-directory'])
def test_cov_launcher_local_code_argv_keeps_independent_cwd_and_artifacts(tmp_path,monkeypatch,directory):
    from scidiscovery.research_loop import contracts as c, experiment_client, local_experiments
    case=_fixed_cov_launcher(tmp_path,monkeypatch,local=True,directory=directory)
    from test_mutable_run_inputs import _frozen_material
    material,proof,original=_frozen_material(case.root,case.work,case.cwd/'input.txt',name='cov-local-input')
    job=c.read(case.root/'jobs/001.json');job['global_materials']=[material]
    c.write(case.root/'jobs/001.json',job)
    case.accepted['snapshot_files']=[dict(proof,name='input.txt')];c.write(case.record/'accepted.json',case.accepted)
    private=case.root/'private.txt';private.write_text('host private fixture')
    case.source.unlink();case.source.symlink_to(private)
    requested=[];reference=experiment_client.input_reference
    def scientific_input(config,path):
        requested.append(Path(path));assert Path(path)==case.cwd/'input.txt'
        return reference(config,path)
    monkeypatch.setattr(experiment_client,'input_reference',scientific_input)
    argv,host_cwd,finish=case.launch(case.config,case.accepted,case.accepted['command'],case.accepted['cwd'])
    bindings=_cov_argv_bindings(argv)
    assert ['--ro-bind',str(case.export),str(case.work/'code')] in bindings
    assert ['--ro-bind',proof['path'],str(case.cwd/'input.txt')] in bindings
    artifacts=local_experiments.artifact_directory(case.config,case.accepted['call_id'])
    assert ['--bind',str(artifacts),str(case.cwd/'artifacts')] in bindings
    assert requested==[case.cwd/'input.txt'] and 'entry' not in case.accepted
    assert argv[argv.index('--chdir')+1]==str(case.cwd)
    assert argv[-3:]==[sys.executable,str(case.source),'explicit-argument']
    assert host_cwd is finish is None and artifacts.is_dir()
    assert not (case.export/'artifacts').exists() and not (case.export/'input.txt').exists()
    if directory:
        assert (case.export/'helper.py').read_text()=="VALUE='fixed helper'\n"
        assert (case.export/'resource.txt').read_text()=='fixed resource'


@pytest.mark.parametrize('changed',['cwd-in-code','artifact-link'])
def test_cov_launcher_local_code_refuses_runtime_alias_before_materialize(tmp_path,monkeypatch,changed):
    valid=_fixed_cov_launcher(tmp_path/'valid',monkeypatch,local=True,directory=True)
    _confirm_cov_argv(valid)
    from scidiscovery.research_loop import contracts as c
    case=_fixed_cov_launcher(tmp_path/'broken',monkeypatch,local=True,directory=True)
    if changed=='cwd-in-code':
        case.accepted['cwd']=case.accepted['experiment_directory']=str(case.work/'code')
    else:(case.cwd/'artifacts').symlink_to(case.work/'code')
    c.write(case.record/'accepted.json',case.accepted)
    with pytest.raises(e.UnsupportedExecution):
        case.launch(case.config,case.accepted,case.accepted['command'],case.accepted['cwd'])
    assert case.materialized==[] and not (case.export/'artifacts').exists()


@pytest.mark.parametrize('phase',['launch','finish'])
@pytest.mark.parametrize('name',['train.csv','val.csv','meta.json'])
def test_cov_runtime_harness_roles_are_checked_at_actual_consumption(tmp_path,monkeypatch,phase,name):
    valid=_fixed_cov_launcher(tmp_path/'valid',monkeypatch);_fixed_cov_harness_roles(valid)
    _confirm_cov_argv(valid)
    case=_fixed_cov_launcher(tmp_path/'broken',monkeypatch);_,protocol=_fixed_cov_harness_roles(case)
    saved=(case.record/'accepted.json').read_bytes();protocol.unlink()
    if phase=='finish':
        _,_,finish=case.launch(case.config,case.accepted,case.accepted['command'],case.accepted['cwd'])
        (case.record/'backend-science/outputs/row.json').write_text(json.dumps(dict(script=str(case.source),run_id=1,ok=True)))
    path=Path(case.settings['public_root'])/'environment/harness'/name
    replacement=path.with_name(path.name+'.replacement');replacement.write_bytes(path.read_bytes());replacement.replace(path)
    with pytest.raises(e.IntegrityError,match='public harness input differs'):
        if phase=='launch':case.launch(case.config,case.accepted,case.accepted['command'],case.accepted['cwd'])
        else:finish(0,True,True)
    assert (case.record/'accepted.json').read_bytes()==saved
    assert not (case.record/'receipt.json').exists() and not (case.record/'process.json').exists()
    if phase=='launch':assert case.materialized==[] and not (case.record/'backend-science').exists()


def _legacy_cov_inputs(case):
    """Real retained call slots with the historical public path/size inventory."""
    from scidiscovery.research_loop import contracts as c
    items,_=_fixed_cov_harness_roles(case);runner=case.accepted['receipt_transport']['runner']
    roles=[dict({key:runner[key] for key in ('path','size_bytes')},name='replay/harness/runner.py'),*items]
    originals={}
    for item in roles:
        source=Path(item['path']);target=case.record/'inputs'/item['name'];raw=source.read_bytes()
        c._bind_original(source,target,root=case.root,size_bytes=item['size_bytes'],source_kind='mutable')
        assert target.read_bytes()==raw and not target.samefile(source)
        originals[item['name']]=dict(public=source,retained=target,raw=raw)
    source=case.record/'source.py'
    c._bind_original(case.source,source,root=case.root,size_bytes=case.source.stat().st_size,source_kind='mutable')
    case.accepted.pop('code_version',None);case.accepted['script']=c.file_ref(source)
    case.accepted['prepared']=dict(local_path=str(case.source),size_bytes=source.stat().st_size)
    case.accepted['snapshot_files']=[{key:item[key] for key in ('name','path','size_bytes')} for item in roles]
    runtime=case.accepted['replay_runtime']
    case.accepted['replay_runtime']={key:runtime[key] for key in ('path','size_bytes','invoked_path')}
    c.write(case.record/'accepted.json',case.accepted)
    return originals


def test_cov_legacy_public_launch_accepts_saved_pathsize_roles_without_a_fingerprint_keyerror(tmp_path,monkeypatch):
    from cov_execution import receipt
    case=_fixed_cov_launcher(tmp_path,monkeypatch);originals=_legacy_cov_inputs(case)
    saved=(case.record/'accepted.json').read_bytes()
    assert all(set(item)=={'name','path','size_bytes'} for item in case.accepted['snapshot_files'])
    argv,host_cwd,finish=case.launch(case.config,case.accepted,case.accepted['command'],case.accepted['cwd'])
    assert host_cwd is None and ['--ro-bind',case.accepted['script']['path'],str(case.source)] in _cov_argv_bindings(argv)
    row=dict(script=str(case.source),run_id=1,ok=True)
    (case.record/'backend-science/outputs/row.json').write_text(json.dumps(row));finish(0,True,True)
    assert json.loads((case.record/'receipt.json').read_text())==receipt.envelope(case.accepted,row)
    assert case.materialized==case.verified==[] and (case.record/'accepted.json').read_bytes()==saved
    assert all(item['retained'].read_bytes()==item['raw'] for item in originals.values())


@pytest.mark.parametrize('missing',['protocol','runtime'])
@pytest.mark.parametrize('phase',['launch','finish'])
def test_cov_optional_replay_input_failure_keeps_science_fingerprints_at_consumption(tmp_path,monkeypatch,missing,phase):
    from cov_execution.experiments import CovExecution, _replay_roles
    from scidiscovery.research_loop import contracts as c,delivery,protocol
    from test_cov_receipt import CLI
    case=_fixed_cov_launcher(tmp_path,monkeypatch)
    harness=Path(case.settings['public_root'])/'environment/harness'
    cli=harness/'exp_cli.py';cli.write_text(CLI)
    for name in ('train.csv','val.csv','meta.json'):(harness/name).write_text('bound '+name)
    for key,name in (('EXP_RUNNER','runner.py'),('EXP_TRAIN','train.csv'),('EXP_VAL','val.csv'),('EXP_META','meta.json')):
        monkeypatch.setenv(key,str(harness/name))
    invoked=case.root/'missing-python' if missing=='runtime' else Path(sys.executable)
    if missing=='protocol':monkeypatch.setattr(protocol,'__file__',str(case.root/'missing-protocol.py'))
    case.config.update(receipt_channel='public_runner_main',run_argv=[str(invoked),'-B','-I',str(cli)],
        runs_root=str(case.root/'runs'),evaluation_key='fixture')
    adapter=object.__new__(CovExecution);adapter.config=case.config;adapter.records=e.Records(case.config)
    metadata=adapter.acceptance_metadata(SimpleNamespace(local_path=str(case.source)),new=True)
    assert metadata['snapshot_files']==[] and metadata['replay_inputs_unavailable'] is True
    inputs=metadata['replay_runtime']['inputs']
    data=[item for item in inputs if item['name'].startswith('replay/harness/')]
    assert [item['name'] for item in data]==['replay/harness/train.csv','replay/harness/val.csv','replay/harness/meta.json']
    assert all({key:item[key] for key in ('path','size_bytes','file_stat')}==e.proof(item['path']) for item in data)
    case.accepted.update(metadata);c.write(case.record/'accepted.json',case.accepted)
    assert {item['name'] for item in data}<={item['name'] for item in _replay_roles(case.accepted)}
    saved=(case.record/'accepted.json').read_bytes()
    def forbidden(*args,**options):pytest.fail('Unavailable optional replay input started a delivery leaf')
    monkeypatch.setattr(delivery,'_call',forbidden)
    assert CovExecution.replay_check.__wrapped__(adapter,case.accepted['call_id'],deadline_boottime=110.,
        on_process=forbidden)==dict(status='unsupported',note='exact_public_replay_inputs_unavailable',cleanup_confirmed=True)
    assert not (case.record/'engineering-replay').exists()
    if phase=='finish':
        _,_,finish=case.launch(case.config,case.accepted,case.accepted['command'],case.accepted['cwd'])
        (case.record/'backend-science/outputs/row.json').write_text(json.dumps(dict(script=str(case.source),run_id=1,ok=True)))
    path=harness/'train.csv';replacement=path.with_name('same-size-train.csv')
    replacement.write_bytes(path.read_bytes());replacement.replace(path)
    with pytest.raises(e.IntegrityError,match='public harness input differs'):
        if phase=='launch':case.launch(case.config,case.accepted,case.accepted['command'],case.accepted['cwd'])
        else:finish(0,True,True)
    assert (case.record/'accepted.json').read_bytes()==saved and not (case.record/'receipt.json').exists()
    assert not (case.record/'process.json').exists() and not (case.record/'engineering-replay').exists()
    if phase=='launch':assert case.materialized==[] and not (case.record/'backend-science').exists()
