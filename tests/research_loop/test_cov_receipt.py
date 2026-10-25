"""Independent cov receipt producer identity and read-only transport integrity."""
import json
from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import experiments
from cov_execution import receipt as cov_receipt, lineage as cov_lineage
from scidiscovery.research_loop.protocol import CURRENT
from execution_fixtures import execution_registry
from test_cov_lineage import wrapper
from test_local_managed import local

CLI = '''import os,subprocess,sys
from pathlib import Path
RUN_DIR=Path(os.environ['EXP_RUN_DIR']);RUNNER=Path(os.environ['EXP_RUNNER'])
def cmd_run(args):
    script=Path(args.script).resolve()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    rc = subprocess.call([sys.executable, str(RUNNER), str(script)])
    sys.exit(rc)
'''
RUNNER = '''import json,os,runpy,sys
from pathlib import Path
RUN_DIR=Path(os.environ['EXP_RUN_DIR']);RESULTS_LOG=RUN_DIR/'results.jsonl'
def main():
    source=Path(sys.argv[1]).resolve();RUN_DIR.mkdir(parents=True,exist_ok=True)
    rows=[json.loads(line) for line in RESULTS_LOG.read_text().splitlines()] if RESULTS_LOG.exists() else []
    spec=runpy.run_path(str(source));n=len(rows)+1
    rec=dict(run_id=n,script=str(source),ok=not spec.get('failed',False),wall_seconds=.01)
    if rec['ok']:
        model=RUN_DIR/f'model_run{n}.pkl';model.write_bytes(b'original fixture artifact')
        rec.update(model_path=str(model),val_score=.5)
    else:rec['error']='Known scientific implementation failure'
    with open(RESULTS_LOG, 'a') as f:
        f.write(json.dumps(rec) + '\\n')
    print(json.dumps(rec, indent=2))
    sys.exit(0 if rec.get('ok') else 1)
if __name__=='__main__':main()
'''


@pytest.mark.process_e2e
@pytest.mark.parametrize('failed',[False,True])
def test_confirmed_runner_output_takes_actual_record_object_and_keeps_candidate_logs(tmp_path,execution_registry,monkeypatch,failed):
    cli=tmp_path/'exp_cli.py';cli.write_text(CLI)
    runner=tmp_path/'runner.py';runner.write_text(RUNNER)
    runs=tmp_path/'runs';monkeypatch.setenv('EXP_RUN_DIR',str(runs));monkeypatch.setenv('EXP_RUNNER',str(runner))
    value=dict(contract_version=2,feedback_version=1,record_dir=str(tmp_path/'lineage'),runs_root=str(runs),
        run_argv=[sys.executable,'-I',str(cli)],receipt_channel='public_runner_main',
        admission=dict(version=1,mode='hard'),scope=dict(handoff_protocol=CURRENT,step=2,role='W',
            job_ref=dict(path=str(tmp_path/'jobs/002.json')),execution_key=dict(path=str(tmp_path/'jobs/002.json')),mechanism_ref='h001.m1'),parents=[],evaluation_key='fixture')
    config=experiments.freeze(value,'cov_execution:experiment',path=tmp_path/'config.json')
    source=tmp_path/'candidate.py'
    source.write_text('print("ordinary multiline training log")\nprint(\'{"ok":true,"run_id":9001}\')\nfailed='+str(failed)+'\n'+('# legal source padding\n'*13000))
    assert source.stat().st_size>cov_receipt.MAX_BYTES
    from scidiscovery.research_loop import worker_execution_bridge as bridge
    # Synthetic trusted host seam; no namespace-isolation claim.
    token=bridge._LOCAL_LAUNCH.set(lambda config,accepted,command,cwd:(command,cwd,None))
    try:
        result=experiments.dispatch(config,source,new=True)
    finally:
        bridge._LOCAL_LAUNCH.reset(token)
    assert result['status']==('known_failed' if failed else 'known_success')
    directory=Path(config['record_dir'])/'calls'/result['call_id']
    receipt=json.loads((directory/'receipt.json').read_text())
    assert set(receipt)=={'version','call_id','scope','source_ref','row'}
    assert receipt['row']['run_id']==1 and receipt['row']['ok'] is not failed
    assert '9001' in (directory/'stdout.raw').read_text()
    assert cov_lineage._completed(directory)['run_id']==1
    if not failed:
        records = experiments.Records(config)
        public_model = runs / 'model_run1.pkl'
        frozen_model = directory / 'model.pkl'
        assert public_model.stat().st_ino != frozen_model.stat().st_ino
        observation = json.loads((directory / 'execution-observation.json').read_text())
        observed = next(item for item in observation['proofs'] if item['source']['path'] == str(frozen_model))
        retained = Path(observed['path'])
        assert retained.stat().st_ino == frozen_model.stat().st_ino
        public_model.write_bytes(b'public producer may later replace its own output')
        assert frozen_model.read_bytes() == b'original fixture artifact'

    before={p:p.read_bytes() for p in directory.iterdir() if p.is_file()}
    adapter=experiments.load(config);adapter.result(result['call_id']);adapter.collect_original(result['call_id'])
    assert before=={p:p.read_bytes() for p in directory.iterdir() if p.is_file()}


def test_public_profile_rejects_unqualified_flags_before_execution(tmp_path,monkeypatch):
    cli=tmp_path/'exp_cli.py';cli.write_text(CLI)
    runner=tmp_path/'runner.py';runner.write_text(RUNNER);monkeypatch.setenv('EXP_RUNNER',str(runner))
    with pytest.raises(ValueError,match='additional interpreter or CLI flags'):
        cov_receipt.profile([sys.executable,'-u',str(cli)],'public_runner_main')
    assert not list(tmp_path.glob('receipt*'))


@pytest.mark.parametrize('flags',[[],['-I'],['-B','-I']])
def test_public_receipt_profile_keeps_exact_legacy_and_bytecode_free_shapes(tmp_path,monkeypatch,flags):
    cli=tmp_path/'exp_cli.py';cli.write_text(CLI)
    runner=tmp_path/'runner.py';runner.write_text(RUNNER);monkeypatch.setenv('EXP_RUNNER',str(runner))
    result=cov_receipt.profile([sys.executable,*flags,str(cli)],'public_runner_main')
    assert result['backend']==cov_receipt.original(cli)
    assert result['runner']==cov_receipt.runner_profile(runner)


@pytest.mark.parametrize('flags',[['-B'],['-I','-B'],['-B','-I','-u']])
def test_public_receipt_profile_does_not_expand_arbitrary_interpreter_options(tmp_path,monkeypatch,flags):
    cli=tmp_path/'exp_cli.py';cli.write_text(CLI)
    runner=tmp_path/'runner.py';runner.write_text(RUNNER);monkeypatch.setenv('EXP_RUNNER',str(runner))
    with pytest.raises(ValueError,match='additional interpreter or CLI flags'):
        cov_receipt.profile([sys.executable,*flags,str(cli)],'public_runner_main')


@pytest.mark.parametrize('failure',[False,True],ids=['complete','fsync-failure'])
def test_scientific_runner_publishes_only_complete_row(tmp_path,monkeypatch,failure):
    runner=tmp_path/'runner.py';runner.write_text(RUNNER)
    source=tmp_path/'candidate.py';source.write_text('failed=False\n')
    runs=tmp_path/'runs';monkeypatch.setenv('EXP_RUN_DIR',str(runs))
    output=tmp_path/'row.json';invocation=tmp_path/'invocation.json'
    invocation.write_bytes(cov_receipt.encoded(dict(runner=str(runner),source=str(source),
        output_line=cov_receipt.runner_profile(runner)['output_line'],output=str(output))))
    monkeypatch.setattr(sys,'argv',list(sys.argv));monkeypatch.setattr(sys,'path',list(sys.path))
    original_fsync=cov_receipt.os.fsync
    calls=[]
    def fsync(descriptor):
        calls.append(output.exists())
        if failure:raise OSError('injected result flush failure')
        original_fsync(descriptor)
    monkeypatch.setattr(cov_receipt.os,'fsync',fsync)
    if failure:
        with pytest.raises(OSError,match='result flush failure'):
            cov_receipt.science_runner(invocation)
        assert not output.exists()
        assert output.with_name('row.json.pending').is_file()
        assert calls==[False]
    else:
        with pytest.raises(SystemExit) as stopped:cov_receipt.science_runner(invocation)
        assert stopped.value.code==0
        row=json.loads(output.read_bytes())
        assert row['script']==str(source) and row['run_id']==1 and row['ok'] is True
        assert not output.with_name('row.json.pending').exists()
        assert calls==[False,True]


def test_receipt_publication_never_replaces_existing_output(tmp_path):
    output=tmp_path/'row.json';output.write_bytes(b'previous exact original')
    with pytest.raises(FileExistsError):cov_receipt.publish(output,b'new bytes')
    assert output.read_bytes()==b'previous exact original'
    assert not output.with_name('row.json.pending').exists()


def test_receipt_publication_and_read_keep_the_same_envelope_bound(tmp_path):
    accepted=dict(call_id='bounded-call',scope={},script={})
    data=cov_receipt.encoded(cov_receipt.envelope(accepted,dict(log='x'*cov_receipt.MAX_BYTES)))
    output=tmp_path/'receipt.json'
    with pytest.raises(ValueError,match='publication bound'):cov_receipt.publish(output,data)
    assert not output.exists() and not (tmp_path/'receipt.json.pending').exists()
    output.write_bytes(data)
    assert cov_lineage._receipt_row(tmp_path,accepted) is None


@pytest.mark.process_e2e
def test_local_managed_finish_failure_retains_stopped_terminal(local):
    from scidiscovery.research_loop import runtime, worker_execution_bridge as bridge
    root,script,config,_=local
    script.write_text('print("finite original execution")\nraise SystemExit(7)\n')
    launches=[];finished=[]
    def launch(configuration,accepted,command,cwd):
        launches.append(accepted['call_id'])
        def finish(returncode,cleanup,transport_complete):
            directory=experiments.Records(config).directory(accepted['call_id'])
            terminal=json.loads((directory/'terminal.json').read_bytes())
            finished.append((returncode,cleanup,transport_complete,terminal))
            raise OSError('injected finish publication failure')
        return command,cwd,finish
    token=bridge._LOCAL_LAUNCH.set(launch)
    try:
        with pytest.raises(OSError,match='finish publication failure'):
            experiments.dispatch(config,script,command_options=dict(experiment_directory=str(root),arguments=[]))
    finally:bridge._LOCAL_LAUNCH.reset(token)
    assert len(launches)==len(finished)==1
    call_id=launches[0];directory=experiments.Records(config).directory(call_id)
    returncode,cleanup,transport_complete,terminal=finished[0]
    assert returncode==7 and cleanup is transport_complete is True
    assert terminal['returncode']==7 and terminal['cleanup_confirmed'] is terminal['transport_complete'] is True
    identity=json.loads((directory/'process.json').read_bytes())
    assert not runtime.process_matches(identity,identity['task_ref'])
    assert (directory/'stdout.raw').read_text()=='finite original execution\n'
    assert not (directory/'completed.json').exists()
    accepted=experiments.Records(config).accepted(call_id)
    adapter=experiments.load(config)
    assert adapter.lookup_submission(experiments.LocalFileDescriptor.model_validate(accepted['prepared']))==(call_id,'unknown')
    assert adapter.result(call_id)['status']=='unknown'
    assert [path.name for path in experiments.Records(config).calls()]==[call_id]
    assert len(launches)==1


@pytest.mark.process_e2e
def test_host_receipt_child_imports_writable_inputs_without_bytecode(tmp_path,execution_registry,monkeypatch):
    inputs=tmp_path/'inputs';inputs.mkdir()
    cli=inputs/'exp_cli.py';cli.write_text(CLI)
    runner=inputs/'runner.py';runner.write_text(RUNNER)
    (inputs/'scientific_dependency.py').write_text('VALUE="inert engineering input"\n')
    flags=tmp_path/'child-flags.json';source=inputs/'candidate.py'
    source.write_text('import json,os,sys\nfrom pathlib import Path\nimport scientific_dependency\n'
        'Path(os.environ["SCID_FIXTURE_FLAGS"]).write_text(json.dumps(dict(isolated=bool(sys.flags.isolated),dont_write_bytecode=sys.dont_write_bytecode,dependency=scientific_dependency.VALUE)))\n')
    before={path.relative_to(inputs):path.read_bytes() for path in inputs.rglob('*') if path.is_file()}
    assert inputs.stat().st_mode & 0o200
    runs=tmp_path/'runs';monkeypatch.setenv('EXP_RUN_DIR',str(runs));monkeypatch.setenv('EXP_RUNNER',str(runner))
    monkeypatch.setenv('SCID_FIXTURE_FLAGS',str(flags));monkeypatch.delenv('PYTHONDONTWRITEBYTECODE',raising=False)
    value=dict(contract_version=2,feedback_version=1,record_dir=str(tmp_path/'lineage'),runs_root=str(runs),
        run_argv=[sys.executable,'-B','-I',str(cli)],receipt_channel='public_runner_main',
        admission=dict(version=1,mode='hard'),scope=dict(handoff_protocol=CURRENT,step=2,role='W',
            job_ref=dict(path=str(tmp_path/'jobs/002.json')),execution_key=dict(path=str(tmp_path/'jobs/002.json')),mechanism_ref='h001.m1'),parents=[],evaluation_key='fixture',
        engineering_check_reserve_seconds=0)
    config=experiments.freeze(value,'cov_execution:experiment',path=tmp_path/'config.json')
    result=experiments.dispatch(config,source,new=True)
    assert result['status']=='known_success'
    observed=json.loads(flags.read_text())
    assert observed==dict(isolated=True,dont_write_bytecode=True,dependency='inert engineering input')
    after={path.relative_to(inputs):path.read_bytes() for path in inputs.rglob('*') if path.is_file()}
    assert before==after and not list(inputs.rglob('__pycache__'))
    accepted=experiments.Records(config).accepted(result['call_id'])
    assert experiments.load(config).command(accepted)[1:3]==['-B','-I']



@pytest.mark.process_e2e
@pytest.mark.parametrize('name',['receipt.json','source.py','row.json'])
def test_known_cov_original_tamper_is_integrity_error_without_reexecution(wrapper,name):
    source=wrapper.script('original')
    result=wrapper.cli('run','--script',source,'--new-plan')
    directory=wrapper.directory(result)
    (directory/name).chmod((directory/name).stat().st_mode|0o200)
    (directory/name).write_bytes((directory/name).read_bytes()+b' ')
    before={p:p.read_bytes() for p in directory.iterdir() if p.is_file()}
    adapter=experiments.load(json.loads(wrapper.config.read_text()),path=wrapper.config)
    with pytest.raises(experiments.IntegrityError):adapter.result(result['call_id'])
    with pytest.raises(experiments.IntegrityError):adapter.collect_original(result['call_id'])
    assert before=={p:p.read_bytes() for p in directory.iterdir() if p.is_file()}


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode',['wrong_run_id','wrong_call_id','wrong_scope','wrong_source_ref','duplicate_receipt'])
def test_malformed_independent_receipt_never_borrows_success_logs_or_replays(wrapper,mode):
    text=wrapper.backend.read_text()
    if mode=='duplicate_receipt':
        text=text.replace('json.dump(envelope,stream)',"json.dump(envelope,stream);stream.write('\\n');json.dump(envelope,stream)")
    else:
        replacement={
            'wrong_run_id':"envelope['row']=dict(returned,run_id=999)",
            'wrong_call_id':"envelope['call_id']='unrelated-call'",
            'wrong_scope':"envelope['scope']={}",
            'wrong_source_ref':"envelope['source_ref']={}",
        }[mode]
        text=text.replace(" with open(receipt,'x')",' '+replacement+"\n with open(receipt,'x')")
    wrapper.backend.write_text(text)
    result=wrapper.run('bad-receipt',mode,'--new-plan')
    assert result['status']=='known_success' and result['candidate_id'] is None
    assert result['result']['status']=='unavailable' and result['returncode']==0
    assert result['run_id'] is None
    directory=wrapper.directory(result)
    assert json.loads((directory/'stdout.raw').read_text())['ok'] is True
    calls=wrapper.calls()
    adapter=experiments.load(json.loads(wrapper.config.read_text()),path=wrapper.config)
    assert adapter.status_details(result['call_id'])['state']=='succeeded'
    assert wrapper.calls()==calls and len(wrapper.accepted())==1


@pytest.mark.parametrize('missing',[False,True],ids=['four-replay-roles','optional-input-unavailable'])
def test_cov_acceptance_keeps_public_harness_roles_outside_scientific_snapshots(tmp_path,monkeypatch,missing):
    from copy import deepcopy
    from types import SimpleNamespace
    from cov_execution.experiments import CovExecution, _replay_roles
    from scidiscovery.research_loop import protocol
    harness=tmp_path/'public/environment/harness';harness.mkdir(parents=True)
    cli=harness/'exp_cli.py';cli.write_text(CLI)
    runner=harness/'runner.py';runner.write_text(RUNNER)
    for name in ('train.csv','val.csv','meta.json'):(harness/name).write_text('inert public '+name)
    for key,name in (('EXP_RUNNER','runner.py'),('EXP_TRAIN','train.csv'),('EXP_VAL','val.csv'),('EXP_META','meta.json')):
        monkeypatch.setenv(key,str(harness/name))
    if missing:(harness/'meta.json').unlink()
    config=dict(receipt_channel='public_runner_main',run_argv=[sys.executable,'-B','-I',str(cli)],
        runs_root=str(tmp_path/'runs'),evaluation_key='fixture')
    before=deepcopy(config);adapter=object.__new__(CovExecution);adapter.config=config
    accepted=adapter.acceptance_metadata(SimpleNamespace(local_path='/science/code/source.py'),new=True)
    assert config==before and accepted['config_record']==before and accepted['snapshot_files']==[]
    assert accepted['receipt_transport']['backend']==cov_receipt.original(cli)
    assert accepted['receipt_transport']['runner']==cov_receipt.runner_profile(runner)
    if missing:
        assert accepted['replay_inputs_unavailable'] is True and 'replay_runtime' not in accepted
        return
    runtime=accepted['replay_runtime']
    assert runtime['invoked_path']==str(Path(sys.executable).absolute())
    assert {key:runtime[key] for key in ('path','size_bytes','file_stat')}==experiments.proof(Path(sys.executable).resolve())
    inputs=runtime['inputs']
    assert [item['name'] for item in inputs]==['replay/harness/train.csv','replay/harness/val.csv',
        'replay/harness/meta.json','replay/scid-current-protocol']
    assert [item['path'] for item in inputs]==[str(harness/name) for name in ('train.csv','val.csv','meta.json')]+[str(Path(protocol.__file__).resolve())]
    assert all({key:item[key] for key in ('path','size_bytes','file_stat')}==experiments.proof(item['path']) for item in inputs)
    roles=_replay_roles(accepted)
    assert len(roles)==5 and roles[0]['name']=='replay/harness/runner.py'
    assert {key:roles[0][key] for key in ('path','size_bytes')}==cov_receipt.original(runner)
    assert set(roles[0])=={'path','size_bytes','name'}
    assert 'file_stat' not in accepted['receipt_transport']['runner']


def _fixed_cov_completion(tmp_path,monkeypatch,*,status='known_success',legacy=False):
    """Complete inert transport originals; no registered/native execution."""
    from copy import deepcopy
    from cov_execution.experiments import CovExecution
    from scidiscovery.research_loop import contracts as c
    from test_cov_command_contract import _fixed_cov_launcher
    case=_fixed_cov_launcher(tmp_path,monkeypatch)
    job_path=case.root/'jobs/001.json'
    c.write(job_path,dict(c.read(job_path),handoff_protocol=CURRENT))
    job_ref=c.record_ref(job_path)
    # Full current scope for this inert host-owned call; the retained job is
    # also its execution key because this fixture has no native task process.
    case.config['scope'].update(handoff_protocol=CURRENT,job_ref=job_ref,
        execution_key=dict(job_ref),mechanism_ref=None)
    runs=case.root/'runs';runs.mkdir()
    case.config.update(runs_root=str(runs),evaluation_key='fixture',parents=[])
    accepted=case.accepted
    if legacy:
        accepted.pop('code_version');source=case.source
    else:source=case.export/'run.py'
    accepted['script']=experiments.proof(source)
    accepted['prepared']=dict(local_path=str(source),size_bytes=accepted['script']['size_bytes'])
    # Actual adapters receive configuration bytes without the host interface;
    # the accepted control original freezes that interface separately.
    accepted.update(change_intent='exact fixture change',runs_root=str(runs),
        interface=deepcopy(case.config['execution_interface']),
        config_record=deepcopy({key:value for key,value in case.config.items() if key!='execution_interface'}))
    (case.record/'accepted.json').write_bytes(experiments.encoded(accepted))
    for name in ('stdout.raw','stderr.raw'):(case.record/name).write_bytes(b'ordinary inert log\n')
    code=1 if status=='known_failed' else 0
    terminal=dict(call_id=accepted['call_id'],scope=accepted['scope'],returncode=code,
        cleanup_confirmed=True,transport_complete=True)
    (case.record/'terminal.json').write_bytes(experiments.encoded(terminal))
    (case.record/'process.json').write_bytes(experiments.encoded(dict(
        task_ref=dict(path=str(case.record/'accepted.json')),pid=1,start_ticks=1,pgid=1,sid=1)))
    row=dict(script=accepted['source_script'],run_id=1,ok=code==0,wall_seconds=.01)
    if code==0:
        model=runs/'model_run1.pkl';model.write_bytes(b'inert exact model original')
        row.update(model_path=str(model),val_score=.5)
    if status!='unknown':
        (case.record/'receipt.json').write_bytes(cov_receipt.encoded(cov_receipt.envelope(accepted,row)))
        (runs/'results.jsonl').write_bytes(experiments.encoded(row))
    adapter=object.__new__(CovExecution);adapter.config=case.config;adapter.records=experiments.Records(case.config)
    case.adapter=adapter;case.source_original=source;case.row=row;case.returncode=code
    return case


@pytest.mark.parametrize('status',['known_success','known_failed','unknown'])
def test_cov_git_completion_and_metadata_survive_removed_draft_and_export(tmp_path,monkeypatch,status):
    case=_fixed_cov_completion(tmp_path,monkeypatch,status=status)
    assert 'execution_interface' not in case.accepted['config_record']
    assert case.accepted['interface']==case.config['execution_interface']
    case.source.unlink();case.source_original.unlink()
    before=(case.record/'accepted.json').read_bytes()
    result=case.adapter.completion(case.record,case.accepted,case.returncode,True,True)
    assert result['status']==('known_success' if status=='unknown' else status)
    assert case.accepted['script'] not in result['proofs']
    if status=='unknown':
        assert result['result']['status']=='unavailable'
        assert result['candidate_id'] is result['run_id'] is result['model'] is None
    if status=='known_success':
        assert result['model']['path']==case.row['model_path']
        assert Path(result['model']['path']).read_bytes()==Path(case.row['model_path']).read_bytes()
    expected={'accepted.json','stdout.raw','stderr.raw','terminal.json'}
    if status!='unknown':expected|={'receipt.json','row.json'}
    assert expected<={Path(item['path']).name for item in result['proofs']}
    (case.record/'completed.json').write_bytes(experiments.encoded(result))
    assert cov_lineage._completed(case.record)==result
    monkeypatch.setattr(experiments,'registration',lambda *_:pytest.fail('Cov observer already owns its source provider'))
    observed=cov_lineage.observe(dict(root=str(case.root),step=1,config=dict(
        runs='runs',calls='project/nodes',evaluation_key='fixture')))
    context=observed['contexts'][0]
    assert context['artifact'] is None and context['source_code_version']==case.version
    assert all(set(proof)=={'path','size_bytes'} for proof in context['proofs'])
    if context['model'] is not None:assert set(context['model'])=={'path','size_bytes'}
    if status=='unknown':
        assert context['result']['status']=='unavailable'
        assert context['candidate_id'] is context['run_id'] is context['model'] is None
    assert cov_lineage._completed(case.record)==result
    assert (case.record/'accepted.json').read_bytes()==before
    assert case.materialized==case.verified==[] and not (case.record/'engineering-replay').exists()


def test_provider_source_projection_keeps_authority_signatures_and_compiler_conflict(tmp_path,monkeypatch):
    from cov_execution.experiments import CovExecution
    case=_fixed_cov_completion(tmp_path,monkeypatch)
    accepted=dict(case.accepted,execution_authority=dict(path='must-be-validated'))
    calls=[]
    def metadata(config,original,*,verify_signatures):
        calls.append((config,original,verify_signatures))
        return dict(subject={},compiled={},compiler={},code_version=dict(case.version,commit='b'*40))
    monkeypatch.setattr(experiments,'_source_control_metadata',metadata)
    monkeypatch.setattr(experiments,'registration',lambda *_:pytest.fail('Selected provider must not redispatch registry'))
    with pytest.raises(experiments.IntegrityError,match='conflicting accepted and compiler code versions'):
        experiments._source_projection_from_provider(case.config,accepted,CovExecution.scientific_source)
    assert calls==[(case.config,accepted,True)]


@pytest.mark.parametrize('damage',['receipt.json','terminal.json','row.json','model','missing-receipt-proof','missing-terminal-proof'])
def test_cov_git_completion_without_export_keeps_mandatory_transport_and_model_proofs(tmp_path,monkeypatch,damage):
    case=_fixed_cov_completion(tmp_path,monkeypatch)
    case.source_original.unlink()
    result=case.adapter.completion(case.record,case.accepted,0,True,True)
    (case.record/'completed.json').write_bytes(experiments.encoded(result))
    assert cov_lineage._completed(case.record)==result
    if damage.startswith('missing-'):
        name='receipt.json' if damage=='missing-receipt-proof' else 'terminal.json'
        result['proofs']=[item for item in result['proofs'] if Path(item['path']).name!=name]
    (case.record/'completed.json').write_bytes(experiments.encoded(result))
    if not damage.startswith('missing-'):
        path=Path(result['model']['path']) if damage=='model' else case.record/damage
        path.chmod(path.stat().st_mode|0o200);path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(experiments.IntegrityError):cov_lineage._completed(case.record)
    assert case.materialized==case.verified==[]


def test_cov_legacy_completion_keeps_source_original_required(tmp_path,monkeypatch):
    case=_fixed_cov_completion(tmp_path,monkeypatch,legacy=True)
    result=case.adapter.completion(case.record,case.accepted,0,True,True)
    assert case.accepted['script'] in result['proofs']
    (case.record/'completed.json').write_bytes(experiments.encoded(result))
    case.source_original.unlink()
    with pytest.raises((ValueError,OSError)):cov_lineage._completed(case.record)
    assert case.materialized==case.verified==[]


@pytest.mark.parametrize('invalid',['row-shape','nonfinite','overflow','ledger-list','ledger-null','ledger-missing-run-id'])
def test_cov_invalid_receipt_output_preserves_native_terminal(tmp_path,monkeypatch,invalid):
    case=_fixed_cov_completion(tmp_path,monkeypatch)
    if invalid.startswith('ledger-'):
        row={'ledger-list':[],'ledger-null':None,'ledger-missing-run-id':dict(ok=True)}[invalid]
        (case.root/'runs/results.jsonl').write_bytes(json.dumps(row).encode()+b'\n')
    else:
        if invalid=='row-shape':row=[]
        else:row=dict(case.row,extra=float('nan') if invalid=='nonfinite' else float('inf'))
        data=json.dumps(cov_receipt.envelope(case.accepted,row)).encode()
        if invalid=='overflow':data=data.replace(b'Infinity',b'1e999')
        (case.record/'receipt.json').write_bytes(data)
        if isinstance(row,dict):
            ledger=json.dumps(row).encode()
            if invalid=='overflow':ledger=ledger.replace(b'Infinity',b'1e999')
            (case.root/'runs/results.jsonl').write_bytes(ledger+b'\n')
    result=case.adapter.completion(case.record,case.accepted,0,True,True)
    assert result['status']=='known_success' and result['returncode']==0
    assert result['result']['status']=='unavailable'
    assert result['candidate_id'] is result['run_id'] is result['model'] is None
    (case.record/'completed.json').write_bytes(experiments.encoded(result))
    assert cov_lineage._completed(case.record)==result


def test_cov_ambiguous_ledger_keeps_its_integrity_error(tmp_path,monkeypatch):
    case=_fixed_cov_completion(tmp_path,monkeypatch)
    (case.root/'runs/results.jsonl').write_bytes(experiments.encoded(case.row)*2)
    with pytest.raises(experiments.IntegrityError,match='run identities are ambiguous'):
        case.adapter.completion(case.record,case.accepted,0,True,True)
    assert not (case.record/'completed.json').exists()


@pytest.mark.parametrize('damage',['none','partial','foreign','ledger','cleanup','transport','returncode'])
def test_cov_completion_recovers_only_same_stopped_pending_receipt(tmp_path,monkeypatch,damage):
    case=_fixed_cov_completion(tmp_path,monkeypatch)
    final=case.record/'receipt.json';pending=case.record/'receipt.pending'
    final.rename(pending)
    if damage=='partial':pending.write_bytes(b'{"version":2,')
    elif damage=='foreign':
        value=json.loads(pending.read_bytes());value['call_id']='other-call'
        pending.write_bytes(cov_receipt.encoded(value))
    elif damage=='ledger':
        (case.root/'runs/results.jsonl').write_bytes(experiments.encoded(dict(case.row,wall_seconds=99)))
    terminal=json.loads((case.record/'terminal.json').read_bytes())
    if damage=='cleanup':terminal['cleanup_confirmed']=False
    elif damage=='transport':terminal['transport_complete']=False
    elif damage=='returncode':terminal['returncode']=1
    (case.record/'terminal.json').write_bytes(experiments.encoded(terminal))
    pending_bytes=pending.read_bytes();terminal_bytes=(case.record/'terminal.json').read_bytes()
    monkeypatch.setattr(case.adapter,'command',lambda *_:pytest.fail('Recovery must not execute a command'))
    result=case.adapter.completion(case.record,case.accepted,terminal['returncode'],
        terminal['cleanup_confirmed'],terminal['transport_complete'])
    assert (case.record/'terminal.json').read_bytes()==terminal_bytes
    if damage=='none':
        assert result['status']=='known_success' and result['candidate_id']=='run-1' and result['model']
        assert final.read_bytes()==pending_bytes and not pending.exists()
    else:
        assert not final.exists() and pending.read_bytes()==pending_bytes
        assert result['result']['status']=='unavailable'
        assert result['candidate_id'] is result['run_id'] is result['model'] is None
        assert result['status']==('unknown' if damage in {'cleanup','transport'} else
            'known_failed' if damage=='returncode' else 'known_success')


@pytest.mark.parametrize('matching',[False,True])
def test_pending_recovery_preserves_existing_receipt(tmp_path,monkeypatch,matching):
    case=_fixed_cov_completion(tmp_path,monkeypatch)
    final=case.record/'receipt.json';original=final.read_bytes()
    pending=case.record/'receipt.pending'
    value=json.loads(original)
    if not matching:value['call_id']='other-call'
    data=cov_receipt.encoded(value);pending.write_bytes(data)
    terminal=json.loads((case.record/'terminal.json').read_bytes())
    cov_receipt.recover_pending(case.record,case.accepted,terminal,[case.row])
    assert final.read_bytes()==original
    if matching:assert not pending.exists()
    else:assert pending.read_bytes()==data


@pytest.mark.parametrize('returncode',[0,1])
def test_cov_terminal_only_reconciliation_preserves_execution_without_candidate(tmp_path,monkeypatch,returncode):
    from cov_execution.experiments import validate_context
    case=_fixed_cov_completion(tmp_path,monkeypatch,status='unknown')
    terminal=json.loads((case.record/'terminal.json').read_bytes());terminal['returncode']=returncode
    (case.record/'terminal.json').write_bytes(experiments.encoded(terminal))
    terminal_bytes=(case.record/'terminal.json').read_bytes()
    # An unguarded raw scientific row is not a host receipt and cannot be recovered.
    row=case.record/'backend-science/outputs/row.json';row.parent.mkdir(parents=True)
    row.write_bytes(experiments.encoded(case.row))
    monkeypatch.setattr(case.adapter,'command',lambda *_:pytest.fail('Reconciliation must not execute a command'))
    result=case.adapter.result(case.accepted['call_id'])
    assert result['status']==('known_success' if returncode==0 else 'known_failed')
    assert result['result']['status']=='unavailable'
    assert result['candidate_id'] is result['run_id'] is result['model'] is None
    assert not (case.record/'receipt.json').exists()
    assert (case.record/'terminal.json').read_bytes()==terminal_bytes
    details=case.adapter.status_details(case.accepted['call_id'])
    assert details['state']==('succeeded' if returncode==0 else 'failed')
    assert details['returncode']==returncode and details['result']['status']=='unavailable'
    assert details['candidate_id'] is details['metric'] is None
    feedback=case.adapter.public_result(result)
    assert feedback['model'] is None and feedback['result']['status']=='unavailable'
    assert case.adapter.result(case.accepted['call_id'])==result
    assert cov_lineage._completed(case.record)==result
    validate_context(result,case.accepted,None)
    with pytest.raises(ValueError,match='parent_plan_unresolved'):
        cov_lineage._parent(case.config,'run-1',False)
    qualified=dict(result,candidate_id='run-1',run_id=1)
    with pytest.raises(experiments.IntegrityError,match='acquired scientific qualification'):
        validate_context(qualified,case.accepted,None)


@pytest.mark.parametrize('returncode',[0,1])
@pytest.mark.parametrize('ledger',['missing','invalid-json','invalid-row'])
def test_cov_unavailable_context_survives_core_lineage_without_candidate(tmp_path,monkeypatch,returncode,ledger):
    from types import SimpleNamespace
    from cov_execution.experiments import validate_context
    from scidiscovery.research_loop import history, lineage, originals, protocol
    case=_fixed_cov_completion(tmp_path,monkeypatch,status='unknown')
    terminal=json.loads((case.record/'terminal.json').read_bytes());terminal['returncode']=returncode
    (case.record/'terminal.json').write_bytes(experiments.encoded(terminal))
    result=case.adapter.result(case.accepted['call_id'])
    if ledger!='missing':
        (case.root/'runs/results.jsonl').write_bytes(b'{invalid\n' if ledger=='invalid-json' else b'null\n')
    def archive(name):
        proof=experiments.proof(case.root/name)
        return dict(proof,path=name,snapshot=name,retention='archived')
    monkeypatch.setattr(protocol,'stage_scope',lambda *_:case.accepted['scope'])
    monkeypatch.setattr(history,'_execution_interface',lambda *_:dict(executor='cov_execution:experiment'))
    monkeypatch.setattr(experiments,'registration',lambda *_:pytest.fail('Cov observer already owns its source provider'))
    monkeypatch.setattr(originals,'resolve_root_ref',lambda root,proof:root/proof['snapshot'])
    monkeypatch.setattr(originals,'resolve_ref',lambda source,proof:case.root/proof['snapshot'])
    job=dict(step=1,role='W',direct_refs=[])
    observation=cov_lineage.observe(dict(root=str(case.root),step=1,config=dict(
        runs='runs',calls='project/nodes',evaluation_key='fixture')))
    assert observation['candidates']==[] and observation['coverage']['status']=='partial'
    assert all(set(proof)=={'path','size_bytes'} for proof in observation['contexts'][0]['proofs'])
    monkeypatch.setattr(experiments,'registration',lambda *_:(SimpleNamespace(context_validator=validate_context),None))
    contexts=lineage.contexts(case.root,job,observation['contexts'],archive)
    context=contexts[0]
    assert context['status']==('known_success' if returncode==0 else 'known_failed')
    assert context['returncode']==returncode and context['result']==result['result']
    assert context['candidate_id'] is context['run_id'] is context['model'] is None
    observation['contexts']=contexts
    monkeypatch.setattr(history,'read_observation',lambda *_:observation)
    monkeypatch.setattr(originals,'node',lambda *_:SimpleNamespace(origin_identity={},inherited=False))
    monkeypatch.setattr(originals,'identity_key',lambda *_:'exact-fixture-origin')
    sources=[(job,dict(summary='Native terminal only',limitations=[]),dict(observations=True,raw_ref={}),None)]
    graph=lineage.build(case.root,2,sources=sources,search_state=dict(mechanisms={}))
    assert graph['candidates']=={} and graph['relations']==[] and graph['coverage']=='partial'
    assert observation['contexts'][0]['result']['status']=='unavailable'
    unmarked=dict(context);unmarked.pop('result');observation['contexts']=[unmarked]
    with pytest.raises(ValueError,match='Context has no observed candidate'):
        lineage.build(case.root,2,sources=sources,search_state=dict(mechanisms={}))
