"""Small real-CLI wrapper checks; no scientific-role launcher or model calls."""
from scidiscovery.research_loop.protocol import CURRENT
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
 envelope=dict(version=2,call_id=os.environ['SCID_COV_CALL_ID'],scope=json.loads(os.environ['SCID_COV_SCOPE']),
  source_ref=json.loads(os.environ['SCID_COV_SOURCE_REF']),row=returned)
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
        scope=dict(handoff_protocol=CURRENT,step=2, role='W', job_ref=dict(path=str(tmp_path/'jobs/002.json')), execution_key=dict(path=str(tmp_path/'jobs/002.json')), mechanism_ref='h001.m1'),
        parents=[], evaluation_key='fixture-public-v1')
    config.write_text(json.dumps(experiments.freeze(value,'cov_execution:experiment',path=config)))
    env = dict(os.environ, PYTHONPATH=str(Path(cov_lineage.__file__).parents[1])+os.pathsep+os.environ.get('PYTHONPATH',''), EXP_WALL_CAP_S='1')

    class Wrapper:
        def cli(self, *args, expected=0):
            # Synthetic host seam: exercise real child receipts, not namespace isolation.
            launch = ('import runpy,sys; from scidiscovery.research_loop import worker_execution_bridge as bridge; '
                      'bridge._LOCAL_LAUNCH.set(lambda config,accepted,command,cwd:(command,cwd,None)); '
                      'sys.argv=["cov_execution.lineage",*sys.argv[1:]]; '
                      'runpy.run_module("cov_execution.lineage",run_name="__main__")')
            reply = subprocess.run([sys.executable, '-c', launch,
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


@pytest.fixture
def cov_completed(wrapper):
    """Real receipt/completion publishers; only physical terminal facts are supplied."""
    from cov_execution.experiments import CovExecution
    from cov_execution.receipt import envelope
    from scidiscovery.operations.execution import LocalFileDescriptor
    from scidiscovery.research_loop import storage_policy as storage

    def create(*, cleanup=True, transport_complete=True, returncode=0):
        config=json.loads(wrapper.config.read_text())
        adapter=CovExecution(config)
        call='fixture-call'
        directory=adapter.records.root/'calls'/call
        directory.mkdir(parents=True)
        source=wrapper.script('receipt-source')
        snapshot=directory/'source.py';snapshot.write_bytes(source.read_bytes());snapshot.chmod(0o400)
        descriptor=LocalFileDescriptor(name=call,local_path=str(source),size_bytes=source.stat().st_size,
            media_type='application/json')
        accepted=dict(call_id=call,scope=config['scope'],parent=dict(status='declared_root',candidate_id=None,source='none'),
            change_intent=None,script=experiments.proof(snapshot),source_script=str(source),
            prepared=descriptor.model_dump(mode='json'),runs_root=str(wrapper.runs),
            interface=config['execution_interface'])
        experiments.write(directory/'accepted.json',accepted)
        adapter.records.publish(call,'process.json',dict(pid=900000001,start_ticks=1,pgid=900000001,sid=900000001,
            boot_id='receipt-physical-terminal-fixture',task_ref=dict(path=str(directory/'accepted.json'))))
        for name in ('stdout.raw','stderr.raw'):
            path=directory/name;path.write_bytes(b'ordinary transport diagnostic\n');path.chmod(0o400)
        wrapper.runs.mkdir(parents=True)
        model=wrapper.runs/'model_run1.pkl';model.write_bytes(b'exact receipt model fixture\n')
        row=dict(run_id=1,script=str(source),ok=True,model_path=str(model),val_score=.5,val_acc=.5)
        (wrapper.runs/'results.jsonl').write_text(json.dumps(row)+'\n')
        experiments.write(directory/'receipt.json',envelope(accepted,row))
        adapter.records.publish(call,'terminal.json',dict(returncode=returncode,cleanup_confirmed=cleanup,
            transport_complete=transport_complete))
        adapter.records.bind_external(call,call,'succeeded')
        result=adapter.completion(directory,accepted,returncode,cleanup,transport_complete,
            context=storage.Deadline(10))
        adapter.records.publish(call,'completed.json',result)
        return dict(adapter=adapter,config=config,accepted=accepted,directory=directory,model=model,
            completed=adapter.result(call),row=row)
    return create


@pytest.mark.parametrize('terminal',[
    dict(cleanup=False),dict(transport_complete=False)],ids=['cleanup-unknown','transport-incomplete'])
def test_receipt_model_requires_exact_same_call_terminal(cov_completed,terminal):
    row=cov_completed(**terminal)
    assert row['completed']['status']=='unknown' and row['completed']['model'] is None
    assert row['model'].stat().st_mode&0o777==0o644
    assert not (row['directory']/'model.pkl').exists()


def test_selected_delivery_objects_are_overlaid_readonly(tmp_path,monkeypatch):
    from cov_execution import delivery
    stage=tmp_path/'stage';(stage/'inputs').mkdir(parents=True);(stage/'scratch').mkdir()
    source=tmp_path/'original.py';source.write_bytes(b'original source\n')
    data=tmp_path/'train.csv';data.write_bytes(b'value\n1\n')
    objects={}
    for label,path in (('source.py',source),('harness/train.csv',data)):
        point=stage/'inputs'/label;point.parent.mkdir(parents=True,exist_ok=True);point.touch(mode=0o400)
        objects[label]=dict(path=str(path),size_bytes=path.stat().st_size)
    python=Path(sys.executable)
    request=dict(scope=dict(handoff_protocol=CURRENT),phase='replay',stage=str(stage),
        config=dict(python=str(python)),deadline_boottime=10**12,object_refs=objects,
        inputs=[dict(path=str(python.resolve()),size_bytes=python.resolve().stat().st_size,
            invoked_path=str(python),label='runtime-python')])
    argv=[]
    class CapturedExec(Exception):pass
    def capture(binary,args):
        argv.extend(args)
        raise CapturedExec()
    monkeypatch.setattr(delivery.shutil,'which',lambda name:'/qualified/bwrap' if name=='bwrap' else None)
    monkeypatch.setattr(delivery.os,'execv',capture)
    with pytest.raises(CapturedExec):delivery.isolate(request,str(Path(delivery.__file__).resolve()))
    for label,reference in objects.items():
        assert any(argv[index:index+3]==['--ro-bind',reference['path'],'/inputs/'+label]
            for index in range(len(argv)-2))
        assert (stage/'inputs'/label).stat().st_size==0
        assert (stage/'inputs'/label).stat().st_ino!=Path(reference['path']).stat().st_ino
    argv.clear()
    request['object_refs']=dict(objects,**{'../escape':objects['source.py']})
    with pytest.raises(ValueError,match='object reference'):delivery.isolate(request,str(Path(delivery.__file__).resolve()))
    assert argv==[]


def test_current_frozen_wrapper_configuration_roundtrips_and_rejects_undeclared_fields(wrapper):
    frozen = json.loads(wrapper.config.read_text())
    parsed = cov_lineage._config(wrapper.config)
    assert parsed == frozen
    invalid = [dict(frozen, undeclared_host_binding='not part of the wrapper contract'),
        {key: value for key, value in frozen.items() if key != 'storage_policy'},
        dict(frozen, storage_policy=dict(frozen['storage_policy'], undeclared_dimension=1)),
        dict(frozen, storage_policy=dict(frozen['storage_policy'], enabled='yes'))]
    for value in invalid:
        wrapper.config.write_text(json.dumps(value))
        with pytest.raises(ValueError):
            cov_lineage._config(wrapper.config)
    assert wrapper.accepted() == [] and wrapper.calls() == b''


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
def test_public_stdout_and_stderr_exact_aggregate_bound_is_allowed(wrapper):
    result = wrapper.run('exact-byte-bound', 'exact_stdio', '--new-plan')
    assert result['status'] == 'known_success'
    directory = wrapper.directory(result)
    assert (directory / 'stdout.raw').stat().st_size + (directory / 'stderr.raw').stat().st_size == cov_lineage.MAX_BYTES


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
