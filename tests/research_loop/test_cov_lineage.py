"""Small real-CLI wrapper checks; no scientific-role launcher or model calls."""
from scidiscovery.research_loop.protocol import CURRENT
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cov_execution import lineage as cov_lineage
from scidiscovery.research_loop import experiments
from execution_fixtures import execution_registry


# The installed probe's tiny public-exp-shaped backend, extended only for exact
# independent per-call receipts and aggregate stdio boundaries. No training or models.
BACKEND = r'''import argparse,json,os,pathlib,sys
p=argparse.ArgumentParser();p.add_argument('--runs-root',type=pathlib.Path,required=True)
s=p.add_subparsers(dest='command',required=True);r=s.add_parser('run');r.add_argument('--script',type=pathlib.Path,required=True)
a=p.parse_args();root=a.runs_root;root.mkdir(parents=True,exist_ok=True);ledger=root/'results.jsonl'
rows=[json.loads(line) for line in ledger.read_text().splitlines() if line.strip()] if ledger.exists() else []
source=a.script.resolve();spec=json.loads(source.read_text());mode=spec.get('mode','success');n=len(rows)+1
row=dict(run_id=n,script=str(source),ok=mode!='failed',wall_seconds=.001)
if mode=='failed':row['error']='Known local training failure'
else:
 model=root/f'model_run{n}.pkl';model.write_bytes(b'fixture:'+source.read_bytes())
 row.update(model_path=str(model),val_score=.5,val_acc=.5)
with (root/'backend-calls.jsonl').open('a') as f:f.write(json.dumps(dict(run_id=n,mode=mode))+'\n')
with ledger.open('a') as f:f.write(json.dumps(row)+'\n')
returned=dict(row)
if mode=='wrong_script':returned['script']=str(root/'unrelated.py')
if mode=='wrong_metric':returned['val_score']=.9
receipt=os.environ.get('SCID_COV_RECEIPT_PATH')
if receipt and mode!='missing_receipt':
 envelope=dict(version=1,call_id=os.environ['SCID_COV_CALL_ID'],accepted_sha256=os.environ['SCID_COV_ACCEPTED_SHA256'],
  source_sha256=os.environ['SCID_COV_SOURCE_SHA256'],row=returned)
 with open(receipt,'x') as stream:json.dump(envelope,stream)
encoded=(json.dumps(returned)+'\n').encode()
if mode=='noise':
 sys.stdout.write('Ordinary training diagnostic before the final report\n')
 sys.stderr.write('Ordinary backend warning, not a receipt\n')
if mode=='json_shaped_logs':
 sys.stdout.write(json.dumps(dict(run_id=987654,ok=True,val_score=.999,script='unrelated-log.py'))+'\n')
 sys.stderr.write(json.dumps(dict(type='diagnostic',metric='not-evidence'))+'\n')
sys.stdout.buffer.write(encoded);sys.stdout.buffer.flush()
if mode=='stdout_overflow':sys.stdout.buffer.write(b'x'*(spec['bound']+1));sys.stdout.buffer.flush()
if mode=='stderr_overflow':sys.stderr.buffer.write(b'e'*(spec['bound']+1));sys.stderr.buffer.flush()
if mode=='exact_stdio':sys.stderr.buffer.write(b'e'*(spec['bound']-len(encoded)));sys.stderr.buffer.flush()
raise SystemExit(1 if mode=='failed' else 0)
'''


@pytest.fixture
def wrapper(tmp_path,execution_registry):
    backend = tmp_path / 'backend.py'
    backend.write_text(BACKEND)
    runs = tmp_path / 'runs'
    records = tmp_path / 'work/lineage'
    config = tmp_path / 'config.json'
    value=dict(receipt_channel='backend_file',admission=dict(version=1,mode='hard'),contract_version=2, feedback_version=1, record_dir=str(records), runs_root=str(runs),
        run_argv=[sys.executable, str(backend), '--runs-root', str(runs)],
        scope=dict(handoff_protocol=CURRENT,step=2, role='W', job_sha256='a'*64, execution_key='b'*64, mechanism_ref='h001.m1'),
        parents=[], evaluation_key='fixture-public-v1')
    config.write_text(json.dumps(experiments.freeze(value,'cov_execution:experiment',path=config)))
    env = dict(os.environ, PYTHONPATH=str(Path(cov_lineage.__file__).parents[1])+os.pathsep+os.environ.get('PYTHONPATH',''), EXP_WALL_CAP_S='1')

    class Wrapper:
        def cli(self, *args, expected=0):
            reply = subprocess.run([sys.executable, '-m', 'cov_execution.lineage',
                                    '--config', str(config), *map(str, args)],
                                   cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
            assert reply.returncode == expected, (reply.stdout, reply.stderr)
            return json.loads(reply.stdout)

        def script(self, name, mode='success'):
            path = tmp_path / (name + '.json')
            path.write_text(json.dumps(dict(mode=mode, bound=cov_lineage.MAX_BYTES)))
            return path

        def run(self, name, mode='success', *choices, expected=0):
            return self.cli('run', '--script', self.script(name, mode), *choices, expected=expected)

        def accepted(self):
            return sorted(records.glob('calls/*/accepted.json'))

        def calls(self):
            path = runs / 'backend-calls.jsonl'
            return path.read_bytes() if path.exists() else b''

        def directory(self, result):
            return records / 'calls' / result['call_id']

    fixture = Wrapper()
    fixture.root, fixture.config, fixture.backend, fixture.runs = tmp_path, config, backend, runs
    return fixture


@pytest.mark.process_e2e
def test_same_node_new_parent_branch_and_backtrack_without_carry(wrapper):
    p = wrapper.run('P', 'success', '--new-plan')
    q = wrapper.run('Q', 'success', '--parent-plan', p['candidate_id'], '--change-intent', 'Improve P')
    r = wrapper.run('R', 'success', '--parent-plan', p['candidate_id'])
    omitted = wrapper.run('no-automatic-parent')
    assert p['parent']['status'] == 'declared_root'
    assert q['parent']['candidate_id'] == r['parent']['candidate_id'] == p['candidate_id']
    assert q['candidate_id'] != r['candidate_id']
    assert q['parent']['source'] == r['parent']['source'] == 'same_node_completed_call'
    assert omitted['parent']['status'] == 'unknown'
    assert q['model'] == {'retained': True}
    assert not {'scope', 'proofs'} & q.keys()
    before = {p: p.read_bytes() for p in wrapper.root.rglob('*') if p.is_file()}
    status = wrapper.cli('lineage-status')
    assert len(status['calls']) == 4
    assert before == {p: p.read_bytes() for p in wrapper.root.rglob('*') if p.is_file()}


@pytest.mark.process_e2e
def test_exact_failed_receipt_preserves_known_failure_and_allows_new_repair(wrapper):
    p = wrapper.run('P', 'success', '--new-plan')
    failed = wrapper.run('failed', 'failed', '--parent-plan', p['candidate_id'], expected=1)
    assert failed['status'] == 'known_failed' and failed['run_id'] == 2 and failed['model'] is None
    raw = json.loads((wrapper.directory(failed) / 'row.json').read_text())
    assert raw['ok'] is False and raw['error']
    original = (wrapper.directory(failed) / 'completed.json').read_bytes()
    repaired = wrapper.run('repair', 'success', '--parent-plan', p['candidate_id'], '--change-intent', 'Repair known failure')
    assert repaired['status'] == 'known_success' and repaired['run_id'] == 3
    assert (wrapper.directory(failed) / 'completed.json').read_bytes() == original


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode', ['missing_receipt', 'wrong_script', 'wrong_metric'])
def test_missing_or_mismatched_independent_receipt_never_guesses_or_replays(wrapper, mode):
    unknown = wrapper.run('uncertain', mode, '--new-plan', expected=2)
    assert unknown['status'] == 'unknown' and unknown['candidate_id'] is None
    # Public rows, models and plausible stdout cannot establish call ownership.
    directory=wrapper.directory(unknown)
    assert json.loads((directory/'stdout.raw').read_text())['ok'] is True
    if mode=='missing_receipt':assert not (directory/'receipt.json').exists()
    else:assert (directory/'receipt.json').is_file()
    assert (wrapper.runs / 'model_run1.pkl').exists()
    original_calls, original_accepts = wrapper.calls(), wrapper.accepted()
    snapshot = {p: p.read_bytes() for p in wrapper.root.rglob('*') if p.is_file()}
    assert wrapper.cli('lineage-status')['calls'][0]['status'] == 'unknown'
    assert snapshot == {p: p.read_bytes() for p in wrapper.root.rglob('*') if p.is_file()}
    assert wrapper.run('do-not-replay', expected=2)['status'] == 'unknown'
    assert wrapper.calls() == original_calls and wrapper.accepted() == original_accepts


@pytest.mark.process_e2e
def test_unmanaged_public_row_does_not_create_a_selectable_declared_parent(wrapper):
    source = wrapper.script('ordinary-unmanaged')
    subprocess.run([sys.executable, str(wrapper.backend), '--runs-root', str(wrapper.runs),
                    'run', '--script', str(source)], check=True, capture_output=True, timeout=10)
    before = wrapper.calls()
    unresolved = wrapper.run('cannot-guess-old-parent', 'success', '--parent-plan', 'run-1', expected=2)
    assert unresolved['status'] == 'unresolved'
    assert not wrapper.accepted() and wrapper.calls() == before
    omitted = wrapper.run('ordinary-inputs-are-not-parent')
    assert omitted['status'] == 'known_success' and omitted['parent']['status'] == 'unknown'


@pytest.mark.process_e2e
@pytest.mark.parametrize('name', ['model.pkl', 'source.py', 'stdout.raw', 'row.json', 'receipt.json', 'accepted.json'])
def test_retained_call_asset_tamper_is_hard_error_and_blocks_new_execution(wrapper, name):
    result = wrapper.run('P', 'success', '--new-plan')
    path = wrapper.directory(result) / name
    path.chmod(0o600)
    if name == 'accepted.json':
        value = json.loads(path.read_text())
        value['scope']['execution_key'] = 'c'*64
        path.write_text(json.dumps(value))
    else:
        path.write_bytes(path.read_bytes() + b'tampered')
    before = wrapper.calls()
    if name in {'model.pkl', 'source.py'}:
        preview = wrapper.cli('lineage-status')
        assert preview['calls'][0]['status'] == 'known_success'
        assert 'does not establish source/model integrity' in preview['feedback']['qualification']
    else:
        assert wrapper.cli('lineage-status', expected=3)['status'] == 'integrity_error'
    assert wrapper.run('cannot-use-mutated-parent', 'success', '--parent-plan', result['candidate_id'], expected=3)['status'] == 'integrity_error'
    assert wrapper.calls() == before and len(wrapper.accepted()) == 1


@pytest.mark.process_e2e
def test_public_stdout_and_stderr_exact_aggregate_bound_is_allowed(wrapper):
    result = wrapper.run('exact-byte-bound', 'exact_stdio', '--new-plan')
    assert result['status'] == 'known_success'
    directory = wrapper.directory(result)
    assert (directory / 'stdout.raw').stat().st_size + (directory / 'stderr.raw').stat().st_size == cov_lineage.MAX_BYTES


@pytest.mark.process_e2e
def test_incomplete_transport_rejects_even_exact_successful_receipt(wrapper):
    result = wrapper.run('complete-original', 'success', '--new-plan')
    directory = wrapper.directory(result)
    accepted = json.loads((directory / 'accepted.json').read_text())
    rows = [json.loads(line) for line in (wrapper.runs / 'results.jsonl').read_text().splitlines()]
    original = {path: path.read_bytes() for path in directory.iterdir() if path.is_file()}
    receipt=json.loads((directory/'receipt.json').read_text())
    assert receipt['row']==rows[0]
    assert set(receipt)=={'version','call_id','accepted_sha256','source_sha256','row'}
    assert receipt['version']==1 and receipt['call_id']==accepted['call_id']
    assert receipt['accepted_sha256']==hashlib.sha256(experiments.encoded(accepted)).hexdigest()
    assert receipt['source_sha256']==accepted['script']['sha256']
    assert cov_lineage._receipt(directory, accepted, rows, 0, True, transport_complete=True)['status'] == 'known_success'
    assert cov_lineage._receipt(directory, accepted, rows, 0, True, transport_complete=False) is None
    assert original == {path: path.read_bytes() for path in directory.iterdir() if path.is_file()}


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode', ['stdout_overflow', 'stderr_overflow'])
def test_large_stdio_is_fully_retained_without_losing_known_completion(wrapper, mode):
    result=wrapper.run('over-bound',mode,'--new-plan')
    assert result['status']=='known_success' and result['candidate_id'] is not None
    directory = wrapper.directory(result)
    assert (directory/'stdout.raw').stat().st_size+(directory/'stderr.raw').stat().st_size>cov_lineage.MAX_BYTES
    assert next(row for row in wrapper.cli('lineage-status')['calls'] if row['call_id']==result['call_id'])['status']=='known_success'


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode',['noise','json_shaped_logs'])
def test_ordinary_and_json_shaped_logs_do_not_replace_independent_receipt(wrapper,mode):
    result=wrapper.run('independent-receipt',mode,'--new-plan')
    assert result['status']=='known_success' and result['run_id']==1
    assert result['val_score']==.5
    directory=wrapper.directory(result)
    receipt=json.loads((directory/'receipt.json').read_text())
    assert receipt['row']==json.loads((directory/'row.json').read_text())
    assert receipt['call_id']==result['call_id'] and receipt['row']['run_id']==1
    assert (directory/'stderr.raw').read_bytes()
    assert len((directory/'stdout.raw').read_text().splitlines())==2
    if mode=='json_shaped_logs':assert '987654' in (directory/'stdout.raw').read_text()
    else:assert 'Ordinary training diagnostic' in (directory/'stdout.raw').read_text()
    before=wrapper.calls()
    preview=wrapper.cli('lineage-status')
    assert preview['counts']['known_success']==1 and preview['calls'][0]['metric']==.5
    assert wrapper.calls()==before
