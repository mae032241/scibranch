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


@pytest.mark.parametrize('failed',[False,True])
def test_confirmed_runner_output_takes_actual_record_object_and_keeps_candidate_logs(tmp_path,execution_registry,monkeypatch,failed):
    cli=tmp_path/'exp_cli.py';cli.write_text(CLI)
    runner=tmp_path/'runner.py';runner.write_text(RUNNER)
    runs=tmp_path/'runs';monkeypatch.setenv('EXP_RUN_DIR',str(runs));monkeypatch.setenv('EXP_RUNNER',str(runner))
    value=dict(contract_version=2,feedback_version=1,record_dir=str(tmp_path/'lineage'),runs_root=str(runs),
        run_argv=[sys.executable,'-I',str(cli)],receipt_channel='public_runner_main',
        admission=dict(version=1,mode='hard'),scope=dict(handoff_protocol=CURRENT,step=2,role='W',
            job_sha256='a'*64,execution_key='b'*64,mechanism_ref='h001.m1'),parents=[],evaluation_key='fixture')
    config=experiments.freeze(value,'cov_execution:experiment',path=tmp_path/'config.json')
    source=tmp_path/'candidate.py'
    source.write_text('print("ordinary multiline training log")\nprint(\'{"ok":true,"run_id":9001}\')\nfailed='+str(failed)+'\n'+('# legal source padding\n'*13000))
    assert source.stat().st_size>cov_receipt.MAX_BYTES
    result=experiments.dispatch(config,source,new=True)
    assert result['status']==('known_failed' if failed else 'known_success')
    directory=Path(config['record_dir'])/'calls'/result['call_id']
    receipt=json.loads((directory/'receipt.json').read_text())
    assert set(receipt)=={'version','call_id','accepted_sha256','source_sha256','row'}
    assert receipt['row']['run_id']==1 and receipt['row']['ok'] is not failed
    assert '9001' in (directory/'stdout.raw').read_text()
    assert cov_lineage._completed(directory)['run_id']==1
    if not failed:
        records = experiments.Records(config)
        public_model = runs / 'model_run1.pkl'
        frozen_model = directory / 'model.pkl'
        blob = records.storage_root() / 'host-history/objects' / result['model']['sha256']
        assert public_model.stat().st_ino != frozen_model.stat().st_ino
        assert frozen_model.stat().st_ino == blob.stat().st_ino
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
            job_sha256='a'*64,execution_key='b'*64,mechanism_ref='h001.m1'),parents=[],evaluation_key='fixture',
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
@pytest.mark.parametrize('mode',['wrong_run_id','wrong_call_id','wrong_accepted_digest','wrong_source_digest','duplicate_receipt'])
def test_malformed_independent_receipt_never_borrows_success_logs_or_replays(wrapper,mode):
    text=wrapper.backend.read_text()
    if mode=='duplicate_receipt':
        text=text.replace('json.dump(envelope,stream)',"json.dump(envelope,stream);stream.write('\\n');json.dump(envelope,stream)")
    else:
        replacement={
            'wrong_run_id':"envelope['row']=dict(returned,run_id=999)",
            'wrong_call_id':"envelope['call_id']='unrelated-call'",
            'wrong_accepted_digest':"envelope['accepted_sha256']='0'*64",
            'wrong_source_digest':"envelope['source_sha256']='0'*64",
        }[mode]
        text=text.replace(" with open(receipt,'x')",' '+replacement+"\n with open(receipt,'x')")
    wrapper.backend.write_text(text)
    result=wrapper.run('bad-receipt',mode,'--new-plan',expected=2)
    assert result['status']=='unknown' and result['candidate_id'] is None
    assert result['run_id'] is None
    directory=wrapper.directory(result)
    assert json.loads((directory/'stdout.raw').read_text())['ok'] is True
    calls=wrapper.calls()
    adapter=experiments.load(json.loads(wrapper.config.read_text()),path=wrapper.config)
    assert adapter.status_details(result['call_id'])['state']=='unknown'
    assert wrapper.calls()==calls and len(wrapper.accepted())==1
