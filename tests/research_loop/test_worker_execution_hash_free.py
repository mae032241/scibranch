"""Real execution CLI science views retain exact private verification."""
import copy
import json
from pathlib import Path
import re

import pytest

from execution_fixtures import execution_registry, remote_registry
from test_nonlocal_execution_observation import case
from test_composed_execution_results import composed_registry
from scidiscovery.research_loop import contracts as c, execution, experiments as e


def invoke(capsys, path, *arguments, expected=0):
    assert e.main(['--config', str(path), *map(str, arguments)]) == expected
    captured = capsys.readouterr()
    value = json.loads(captured.out)
    # Test fixtures use UUID operation handles; no mechanical checksum belongs
    # in these framework-produced response objects.
    assert re.search(r'[0-9a-f]{64}', captured.out) is None
    return value


def test_real_main_client_contracts_and_retained_outputs(tmp_path, monkeypatch, remote_registry, capsys):
    root, job, config, _ = case(tmp_path, monkeypatch)
    work = c.workspace(root, 2)
    path = work / 'work/experiment-config.json'
    assert c.read(path) == dict(worker_view_version=1, step=2, attempt=0)
    assert c.read(execution.origin(root, 2) / 'experiment-config.json') == config
    assert not (work/'work/lineage/calls').exists()
    assert not Path(config['record_dir']).is_relative_to(work)
    assert 'config_sha256' in config['execution_interface']
    capabilities = invoke(capsys, path, 'capabilities')
    assert capabilities['contracts'][0]['key'] == 'opaque_input'
    assert 'sha256' not in capabilities['contracts'][0]
    for metadata in (False, True):
        target = work / 'work' / ('metadata.json' if metadata else 'contract.json')
        result = invoke(capsys, path, 'describe', '--key', 'opaque_input', '--output', target,
            *(['--metadata'] if metadata else []))
        assert set(result['contract']) == {'path', 'size_bytes'}
        assert 'sha256' not in c.read(target)
    # A legitimate scientist original can implement/use a hash. View handling
    # must preserve its bytes rather than recursively scrub scientific data.
    original = b'{"algorithm":"sha256","sha256":"' + b'a' * 64 + b'"}'
    source = work / 'work/scientific-source.json'
    source.write_bytes(original)
    result = invoke(capsys, path, 'run', '--payload', source, '--new-plan', '--mechanism', 'P')
    call = result['call_id']
    for command in ('query', 'lookup'):
        assert invoke(capsys, path, command, '--call-id', call)['state'] == 'succeeded'
    collected = invoke(capsys, path, 'collect', '--call-id', call)
    output = collected['outputs'][0]
    assert output['name'] == 'original' and output['retention'] == 'retained_original'
    assert Path(output['path']).is_relative_to(work)
    assert Path(output['path']).read_bytes() == original == source.read_bytes()
    assert invoke(capsys, path, 'reconcile', '--call-id', call)['status'] == 'known_success'
    assert invoke(capsys, path, 'lineage-status')['counts']['known_success'] == 1
    accepted = e.Records(config).accepted(call)
    assert accepted['prepared']['sha256'] == c.digest(source)
    assert 'sha256' in c.read(e.Records(config).directory(call) / 'execution-observation.json')['outputs'][0]



def test_public_configuration_paths_and_actionable_caller_errors(tmp_path,monkeypatch,remote_registry,capsys):
    root,_,config,_=case(tmp_path,monkeypatch)
    work=c.workspace(root,2);monkeypatch.chdir(work)
    client=work/'work/experiment-config.json'
    relative=invoke(capsys,Path('work/experiment-config.json'),'capabilities')
    assert invoke(capsys,client,'capabilities')['contracts']==relative['contracts']
    missing=invoke(capsys,Path('work/missing.json'),'capabilities',expected=2)
    assert missing['status']=='caller_error' and missing['error']=='config_not_found'
    assert 'exp help' in missing['note'] and str(root) not in json.dumps(missing)
    invalid=invoke(capsys,client,'capabilities','--not-an-option','f'*64,expected=2)
    assert invalid['status']=='caller_error' and invalid['accepted'] is False
    assert '--help' in invalid['note'] and str(root) not in json.dumps(invalid)
    link=work/'work/linked-config.json';link.symlink_to(client)
    assert invoke(capsys,link,'capabilities',expected=3)['status']=='integrity_error'
    assert e.Records(config).calls()==[]


def test_thin_help_and_ingress_errors_have_no_execution_effects(tmp_path,monkeypatch,remote_registry,capsys):
    from scidiscovery.research_loop import worker_execution_bridge as bridge
    root,_,config,_=case(tmp_path,monkeypatch)
    work=c.workspace(root,2);slot=work/bridge.SUBMISSION_DIRECTORY
    def tool(*arguments,code=0):
        assert bridge.main(['--submission-directory',str(slot),*map(str,arguments)])==code
        value=json.loads(capsys.readouterr().out)
        assert str(root) not in json.dumps(value) and re.search(r'[0-9a-f]{64}',json.dumps(value)) is None
        return value
    assert tool('help')['status']=='help'
    assert '--script' in tool('help','run')['usage']
    assert tool('run','--help')['status']=='help'
    assert tool('wrong-command',code=2)['status']=='caller_error'
    assert tool('run','--not-an-option',code=2)['status']=='caller_error'
    assert not slot.exists() and e.Records(config).calls()==[]
    outside=tmp_path/'other-science.json';outside.write_text('{}')
    source=work/'work/source-link.json';source.symlink_to(outside)
    with bridge.serve(slot,work/'work/experiment-config.json',work,launch=lambda *args:None):
        for path in (outside,source):
            reply=bridge.client(slot,['run','--payload',str(path),'--new-plan','--mechanism','P'])
            assert reply['code']==2 and reply['result']['status']=='caller_error'
            assert reply['result']['accepted'] is False
        assert e.Records(config).calls()==[]


@pytest.mark.parametrize('mutation', ['attempt', 'step', 'field', 'private_config', 'native_intent'])
def test_real_main_rejects_changed_client_or_private_original(tmp_path, monkeypatch, remote_registry, capsys, mutation):
    root, _, config, _ = case(tmp_path, monkeypatch)
    path = c.workspace(root, 2) / 'work/experiment-config.json'
    if mutation in {'attempt', 'step', 'field'}:
        value = c.read(path)
        if mutation == 'field':value['host_path'] = '/untrusted/config.json'
        else:value[mutation] += 1
        c.write(path, value)
    else:
        target = execution.origin(root, 2) / ('experiment-config.json' if mutation == 'private_config' else 'native-intent.json')
        value = c.read(target)
        if mutation == 'private_config':value['max_seconds'] += 1
        else:value['task_sha256'] = '0' * 64
        c.write(target, value)
    assert invoke(capsys, path, 'capabilities', expected=3)['status'] == 'integrity_error'
    assert not e.Records(config).calls()


def test_private_capability_comparison_still_rejects_change(tmp_path, monkeypatch, remote_registry, capsys):
    root, _, config, _ = case(tmp_path, monkeypatch)
    original = copy.deepcopy(config)
    config['execution_interface']['public_capabilities'][0]['sha256'] = '0' * 64
    # Exercise exact frozen loading, not a standalone JSON cleanup helper.
    with pytest.raises(e.IntegrityError):e.load(config)
    assert original['execution_interface']['public_capabilities'][0]['sha256'] != '0' * 64


def test_unknown_original_cli_does_not_replay(tmp_path, monkeypatch, remote_registry, capsys):
    root, _, config, _ = case(tmp_path, monkeypatch, outcome='unknown')
    work = c.workspace(root, 2);path = work / 'work/experiment-config.json'
    source = work / 'work/source.json';source.write_text('{}')
    result = invoke(capsys, path, 'run', '--payload', source, '--new-plan', '--mechanism', 'P', expected=2)
    call = result['call_id']
    before = {item:item.read_bytes() for item in e.Records(config).directory(call).rglob('*') if item.is_file()}
    assert invoke(capsys, path, 'query', '--call-id', call)['state'] == 'unknown'
    assert invoke(capsys, path, 'reconcile', '--call-id', call, expected=2)['status'] == 'unknown'
    assert len(e.Records(config).calls()) == 1
    assert all(item.read_bytes() == data for item, data in before.items())


def test_fixed_host_bridge_rejects_authority_and_preserves_originals(tmp_path, monkeypatch, remote_registry):
    from scidiscovery.research_loop import worker_execution_bridge as bridge
    root, _, config, _ = case(tmp_path, monkeypatch)
    work=c.workspace(root,2);path=work/'work/experiment-config.json'
    def no_local_launch(*args):raise AssertionError('Remote fixture has no local scientific process')
    endpoint=work/bridge.SUBMISSION_DIRECTORY
    with bridge.serve(endpoint,path,work,launch=no_local_launch):
        assert bridge.client(endpoint,['capabilities'])['code']==0
        for arguments in (['--config','/host/config','capabilities'],
                ['describe','--key','opaque_input','--output','/tmp/not-task-output'],
                ['run','--payload',str(root/'jobs/002.json')]):
            assert bridge.client(endpoint,arguments)['code']==2
        source=work/'work/source.json';source.write_text('{}')
        result=bridge.client(endpoint,['run','--payload',str(source),'--new-plan','--mechanism','P'])
        assert result['code']==0 and result['result']['status']=='known_success'
        assert len(e.Records(config).calls())==1
        native=execution.origin(root,2)/'experiment-config.json'
        changed=c.read(native);changed['max_seconds']+=1;c.write(native,changed)
        assert bridge.client(endpoint,['capabilities'])['code']==3
    assert endpoint.is_dir()  # Ordinary submissions remain readable scientific files.
    assert not list(endpoint.glob('*.sock'))
    assert re.search(rb'[0-9a-f]{64}',(endpoint/'submit.json').read_bytes()) is None


def test_ordinary_submission_claims_do_not_replay_or_rebind(tmp_path,monkeypatch,remote_registry):
    import time
    from scidiscovery.research_loop import worker_execution_bridge as bridge
    root,_,config,_=case(tmp_path,monkeypatch)
    work=c.workspace(root,2);slot=work/bridge.SUBMISSION_DIRECTORY;slot.mkdir()
    source=work/'work/source.json';source.write_text('{}')
    arguments=['run','--payload',str(source),'--new-plan','--mechanism','P']
    def no_local_launch(*args):raise AssertionError('Remote fixture has no local process')
    def wait(reference):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            try:
                result=c.read(slot/'response.json')
                if result.get('submission_ref')==reference:return result
            except FileNotFoundError:pass
            time.sleep(.02)
        raise AssertionError('Ordinary submission result did not arrive')
    c.write(slot/'submit.json',dict(submission_ref='S001',arguments=arguments))
    with bridge.serve(slot,work/'work/experiment-config.json',work,launch=no_local_launch):
        # A retained slot with no original claim must not become a fresh effect.
        assert wait('S001')['result']['status']=='unknown'
        assert e.Records(config).calls()==[]
        c.write(slot/'submit.json',dict(submission_ref='S002',arguments=arguments))
        original=(slot/'submit.json').read_bytes()
        first=wait('S002');assert first['code']==0
        assert len(e.Records(config).calls())==1
        c.write(slot/'submit.json',dict(submission_ref='S003',arguments=['capabilities']))
        assert wait('S003')['code']==0
        (slot/'submit.json').write_bytes(original)
        assert wait('S002')==first
        assert len(e.Records(config).calls())==1
        (slot/'response.json').unlink()
        c.write(slot/'submit.json',dict(submission_ref='S002',arguments=['capabilities']))
        assert wait('S002')['code']==3
        assert len(e.Records(config).calls())==1
    private=Path(config['record_dir']).parent/'worker-submissions/S002'
    assert (private/'submit.json').read_bytes()==original
    assert c.read(private/'binding.json')['attempt']==0
    # Restarting the lifecycle preserves the original cached result and call.
    (slot/'submit.json').write_bytes(original);(slot/'response.json').unlink()
    with bridge.serve(slot,work/'work/experiment-config.json',work,launch=no_local_launch):
        assert wait('S002')==first
        assert len(e.Records(config).calls())==1



def wait_submission(slot,reference,*,code=None):
    import time
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        try:
            result=c.read(slot/'response.json')
            if result.get('submission_ref')==reference and (code is None or result['code']==code):return result
        except FileNotFoundError:pass
        time.sleep(.02)
    raise AssertionError('Ordinary submission response did not arrive')


@pytest.mark.parametrize('filename',['accepted.json','completed.json','source.py'])
def test_cached_submission_revalidates_canonical_originals(tmp_path,monkeypatch,remote_registry,filename):
    from scidiscovery.research_loop import worker_execution_bridge as bridge
    root,_,config,_=case(tmp_path,monkeypatch)
    work=c.workspace(root,2);slot=work/bridge.SUBMISSION_DIRECTORY
    source=work/'work/source.json';source.write_text('{}')
    def no_local_launch(*args):raise AssertionError('Remote fixture has no local process')
    with bridge.serve(slot,work/'work/experiment-config.json',work,launch=no_local_launch):
        result=bridge.client(slot,['run','--payload',str(source),'--new-plan','--mechanism','P'])
        assert result['result']['status']=='known_success'
        original=(slot/'submit.json').read_bytes()
        reference=json.loads(original)['submission_ref']
        assert bridge.client(slot,['capabilities'])['code']==0
        target=e.Records(config).directory(result['result']['call_id'])/filename
        target.chmod(0o600);target.write_bytes(target.read_bytes()+b' ')
        # Reuse is pure retained verification; any factory/backend invocation fails.
        def forbidden(*args,**kwargs):raise AssertionError('Cached result queried a backend')
        monkeypatch.setattr(e,'load',forbidden)
        (slot/'response.json').unlink();(slot/'submit.json').write_bytes(original)
        response=wait_submission(slot,reference,code=3)
        assert response['result']['status']=='integrity_error'
        assert len(e.Records(config).calls())==1


@pytest.mark.parametrize('special',['fifo','symlink'])
def test_special_submission_slot_rejects_without_blocking(tmp_path,monkeypatch,remote_registry,special):
    import os,time
    from scidiscovery.research_loop import worker_execution_bridge as bridge
    root,_,config,_=case(tmp_path,monkeypatch)
    work=c.workspace(root,2);slot=work/bridge.SUBMISSION_DIRECTORY;slot.mkdir()
    original=work/'work/ordinary.json';original.write_text('{}')
    path=slot/'submit.json'
    if special=='fifo':os.mkfifo(path)
    else:path.symlink_to(original)
    started=time.monotonic()
    with pytest.raises(ValueError):
        with bridge.serve(slot,work/'work/experiment-config.json',work,launch=lambda *args:None):
            raise AssertionError('Special submission file was accepted')
    assert time.monotonic()-started<2
    path.unlink()
    with bridge.serve(slot,work/'work/experiment-config.json',work,launch=lambda *args:None):
        if special=='fifo':os.mkfifo(path)
        else:path.symlink_to(original)
        time.sleep(.15)
        path.unlink()
        c.write(path,dict(submission_ref='S001',arguments=['capabilities']))
        assert wait_submission(slot,'S001')['code']==0
    assert time.monotonic()-started<5
    assert e.Records(config).calls()==[]


def test_concurrent_thin_clients_and_manual_overwrite_never_misbind(tmp_path,monkeypatch,remote_registry):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from scidiscovery.research_loop import worker_execution_bridge as bridge
    root,_,config,_=case(tmp_path,monkeypatch)
    work=c.workspace(root,2);slot=work/bridge.SUBMISSION_DIRECTORY
    def no_local_launch(*args):raise AssertionError('Remote fixture has no local process')
    with bridge.serve(slot,work/'work/experiment-config.json',work,launch=no_local_launch):
        with ThreadPoolExecutor(max_workers=2) as pool:
            caps=pool.submit(bridge.client,slot,['capabilities'])
            description=pool.submit(bridge.client,slot,['describe','--key','opaque_input','--metadata','--output',str(work/'work/concurrent.json')])
            assert caps.result(timeout=5)['result']['contracts']
            assert description.result(timeout=5)['result']['key']=='opaque_input'
        entered=threading.Event();release=threading.Event();execute=bridge._execute
        def paused(server,arguments):
            entered.set()
            if not release.wait(5):raise AssertionError('Test scheduling gate timed out')
            return execute(server,arguments)
        monkeypatch.setattr(bridge,'_execute',paused)
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiting=pool.submit(bridge.client,slot,['capabilities'])
            assert entered.wait(3)
            request=c.read(slot/'submit.json')
            request['arguments']=['lineage-status']
            c.write(slot/'submit.json',request)
            release.set()
            with pytest.raises(ValueError,match='response|slot'):
                waiting.result(timeout=5)
        response=wait_submission(slot,request['submission_ref'],code=3)
        assert response['request']==request and response['result']['status']=='integrity_error'
        assert e.Records(config).calls()==[]



def test_response_atomic_replacement_retries_only_the_read(tmp_path,monkeypatch,remote_registry):
    import os
    from scidiscovery.research_loop import worker_execution_bridge as bridge
    root,_,config,_=case(tmp_path,monkeypatch)
    work=c.workspace(root,2);slot=work/bridge.SUBMISSION_DIRECTORY;reply=slot/'response.json'
    original_fstat=os.fstat;observed=0;replaced=False
    def fstat(fd):
        nonlocal observed,replaced
        value=original_fstat(fd)
        try:name=os.readlink(f'/proc/self/fd/{fd}')
        except OSError:return value
        if name==str(reply) and not replaced:
            observed+=1
            if observed==2:
                replaced=True
                # Replace the response after its fd read, before path identity
                # validation: the exact real failure observed in nested v1.
                bridge._publish(reply,reply.read_bytes())
        return value
    with bridge.serve(slot,work/'work/experiment-config.json',work,launch=lambda *args:None):
        monkeypatch.setattr(os,'fstat',fstat)
        assert bridge.client(slot,['capabilities'])['code']==0
        assert replaced
        assert len(list((Path(config['record_dir']).parent/'worker-submissions').iterdir()))==1
        assert e.Records(config).calls()==[]


@pytest.mark.parametrize('invalid',['fifo','symlink','corrupt_json','wrong_request'])
def test_invalid_response_is_not_retried_or_resubmitted(tmp_path,monkeypatch,invalid):
    import os
    from scidiscovery.research_loop import worker_execution_bridge as bridge
    slot=tmp_path/'experiment';slot.mkdir();reply=slot/'response.json'
    request=dict(submission_ref='S001',arguments=['capabilities']);c.write(slot/'submit.json',request)
    original=(slot/'submit.json').read_bytes()
    if invalid=='fifo':os.mkfifo(reply)
    elif invalid=='symlink':
        other=tmp_path/'other.json';other.write_text('{}');reply.symlink_to(other)
    elif invalid=='corrupt_json':reply.write_text('{incomplete')
    else:c.write(reply,dict(submission_ref='S001',request=dict(request,arguments=['lineage-status']),code=0,result={}))
    def forbidden(*args):raise AssertionError('Invalid response caused another submission')
    monkeypatch.setattr(bridge,'_publish',forbidden)
    with pytest.raises(ValueError):bridge.client(slot,['capabilities'])
    assert (slot/'submit.json').read_bytes()==original


@pytest.mark.parametrize('state',['running','queued'])
def test_file_submission_preserves_async_handle_and_terminal_append(tmp_path,monkeypatch,composed_registry,state):
    from scidiscovery.research_loop import worker_execution_bridge as bridge
    root,_,config,_=case(tmp_path,monkeypatch,executor='composed_execution:fixture',
        extra=dict(collect=False,query_state=state))
    work=c.workspace(root,2);slot=work/bridge.SUBMISSION_DIRECTORY
    source=work/'work/source.json';source.write_text('{}')
    def forbidden(*args):raise AssertionError('Async fixture has no local scientific process')
    with bridge.serve(slot,work/'work/experiment-config.json',work,launch=forbidden):
        started=bridge.client(slot,['run','--payload',str(source),'--new-plan','--mechanism','P'])
        assert started['code']==2 and started['result']['status']==state
        call=started['result']['call_id'];run_raw=(slot/'submit.json').read_bytes()
        queried=bridge.client(slot,['query','--call-id',call])
        assert queried['result']['state']==state and queried['result']['call_id']==call
        query_raw=(slot/'submit.json').read_bytes()
        progress=bridge.client(slot,['lineage-status'])
        assert progress['result']['calls'][0]['call_id']==call
        assert progress['result']['calls'][0]['status']==state
        progress_raw=(slot/'submit.json').read_bytes()
        e.load(config).complete(call)
        c.write(slot/'submit.json',dict(submission_ref='S004',arguments=['reconcile','--call-id',call]))
        terminal_raw=(slot/'submit.json').read_bytes()
        assert wait_submission(slot,'S004')['result']['status']=='known_success'
        (e.Records(config).root/'forbid-backend-reads').write_text('Cached responses must use retained originals only')
        # New terminal/observation files do not mutate the pending claim. Its
        # historical response cannot claim current success, but keeps the ID.
        for raw in (run_raw,query_raw,progress_raw):
            (slot/'response.json').unlink();(slot/'submit.json').write_bytes(raw)
            reference=json.loads(raw)['submission_ref']
            cached=wait_submission(slot,reference)
            assert cached['code']==2 and cached['result']['status']=='unknown'
            handles=[row['call_id'] for row in cached['result'].get('calls',[])]
            assert cached['result'].get('call_id')==call or call in handles
        assert len(e.Records(config).calls())==1
        completed=e.Records(config).directory(call)/'completed.json'
        completed.chmod(0o600);completed.write_bytes(completed.read_bytes()+b' ')
        (slot/'response.json').unlink();(slot/'submit.json').write_bytes(terminal_raw)
        assert wait_submission(slot,'S004',code=3)['result']['status']=='integrity_error'
        assert len(e.Records(config).calls())==1


PUBLIC_CLI = '''import argparse,os,subprocess,sys
from pathlib import Path
RUN_DIR=Path(os.environ['EXP_RUN_DIR']);RUNNER=Path(os.environ['EXP_RUNNER'])
def cmd_run(args):
 script=args.script.resolve()
 RUN_DIR.mkdir(parents=True,exist_ok=True)
 rc = subprocess.call([sys.executable, str(RUNNER), str(script)])
 sys.exit(rc)
parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
run=sub.add_parser('run');run.add_argument('--script',type=Path,required=True)
args=parser.parse_args();cmd_run(args)
'''

PUBLIC_RUNNER = '''import json,os,subprocess,sys
from pathlib import Path
RESULTS_LOG=Path(os.environ['EXP_RUN_DIR'])/'results.jsonl'
def main():
 source=Path(sys.argv[1]).resolve()
 child=subprocess.run([sys.executable,str(source)],capture_output=True,text=True)
 print(child.stdout,end='');print(child.stderr,end='',file=sys.stderr)
 rows=RESULTS_LOG.read_text().splitlines() if RESULTS_LOG.exists() else []
 run_id=len(rows)+1;model=RESULTS_LOG.parent/f'model_run{run_id}.pkl'
 model.write_bytes(b'inert engineering transport fixture; no scientific model')
 rec=dict(run_id=run_id,script=str(source),ok=child.returncode==0,wall_seconds=.001,
  model_path=str(model),val_score=.5,val_acc=.5)
 with open(RESULTS_LOG, 'a') as f:
  f.write(json.dumps(rec) + '\\n')
 print(json.dumps(rec, indent=2))
 sys.exit(0 if rec.get('ok') else 1)
if __name__=='__main__':main()
'''

NAMESPACE_CHECKER = r'''import json,os,re,subprocess,sys,time
from pathlib import Path
from scidiscovery.research_loop.worker_execution_bridge import SUBMISSION_DIRECTORY
work=Path.cwd();root=work.parents[2]
assert (work/'work/task-science.txt').read_text()=='this task scientific input remains readable'
for name in ('A-conclusion.json','score.json'):
 try:(root.parent.parent/'sibling-run'/name).read_bytes()
 except OSError:pass
 else:raise AssertionError('Sibling comparison evidence became readable')
slot=work/SUBMISSION_DIRECTORY;assert slot.is_dir()
# A scientist can write this ordinary submission directly; no IPC, checksum,
# configuration manifest, package or extra agent is involved.
(slot/'submit.json').write_text(json.dumps(dict(submission_ref='S001',arguments=['capabilities'])))
assert re.search(rb'[0-9a-f]{64}',(slot/'submit.json').read_bytes()) is None
deadline=time.monotonic()+15
while time.monotonic()<deadline:
 try:
  direct=json.loads((slot/'response.json').read_text())
  if direct.get('submission_ref')=='S001':break
 except FileNotFoundError:pass
 time.sleep(.05)
else:raise AssertionError('Ordinary scientific submission was not processed')
assert direct['code']==0 and direct['result']['contracts'],direct
for name in ('settings.json','settings-identity.json','jobs/'+work.name+'.json','native-evidence/'+work.name+'/native-intent.json'):
 try:(root/name).read_bytes()
 except OSError:pass
 else:raise AssertionError('Host control original was mounted: '+name)
assert not (work/'work/lineage/calls').exists()
assert not (root/'host-runtime/experiment-control').exists()
def exp(*arguments):
 reply=subprocess.run([str(work/'bin/exp'),*map(str,arguments)],capture_output=True,text=True)
 assert reply.returncode==0,(reply.stdout,reply.stderr)
 assert re.search(r'[0-9a-f]{64}',reply.stdout) is None
 return json.loads(reply.stdout)
contracts=exp('capabilities');assert contracts['contracts']
exp('describe','--key','cov_single_source','--metadata','--output',work/'work/contract.json')
source=work/'inert-source.py'
science=[
 'import json,os,socket',
 'from pathlib import Path',
 'work=Path.cwd();root=work.parents[2]',
 'assert not (root/"settings.json").exists()',
 'assert not (root/"native-evidence").exists()',
 'assert (work/"work/task-science.txt").read_text()=="this task scientific input remains readable"',
 'for name in ("A-conclusion.json","score.json"):',
 ' try:(root.parent.parent/"sibling-run"/name).read_bytes()',
 ' except OSError:pass',
 ' else:raise AssertionError("Sibling comparison evidence was mounted")',
 'assert not (work/"work/experiment/submit.json").exists()',
 'assert not (work/"work/experiment/response.json").exists()',
 'assert not Path("/task-result/science-result.schema.json").exists()',
 'probe=json.loads((work/"work/network-probe.json").read_text())',
 'blocked={}',
 'for name in ("inputs",".git",".agents",".codex",".aws"):',
 ' try:(work/name/"backend-forbidden.txt").write_text("forbidden")',
 ' except OSError:blocked[name]=True',
 ' else:blocked[name]=False',
 'assert all(blocked.values()),blocked',
 '(work/"artifacts/backend-scratch.txt").write_text("authorized scratch")',
 'connected=False',
 'try:',
 ' with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as connection:',
 '  connection.settimeout(1);connected=connection.connect_ex(("127.0.0.1",probe["port"]))==0',
 'except OSError:pass',
 'same_netns=os.stat("/proc/self/ns/net").st_ino==probe["host_netns"]',
 'evidence=dict(loopback_matches_profile=connected==probe["network_enabled"],network_namespace_matches_profile=same_netns==probe["network_enabled"],readonly_inputs=blocked["inputs"],protected_metadata=all(blocked[name] for name in (".git",".agents",".codex",".aws")),scratch_writable=True,agent_submission_unavailable=True)',
 'assert all(evidence.values()),evidence',
 '(work/"artifacts/backend-permissions.json").write_text(json.dumps(evidence))',
 'print("inert-scientific-process")']
source.write_text('\n'.join(science)+'\n')
result=exp('run','--script',source,'--new-plan','--mechanism','Fixture mechanism');assert result['status']=='known_success'
call=result['call_id'];assert exp('query','--call-id',call)['state']=='succeeded'
exp('collect','--call-id',call)
backend=json.loads((work/'artifacts/backend-permissions.json').read_text());assert all(backend.values()),backend
for path in work.rglob('*'):
 assert path.name not in {'accepted.json','completed.json','execution-observation.json','compiler.json','author-closeout-facts.json'},str(path)
 if path.is_file() and path.suffix=='.json':
  assert re.search(rb'[0-9a-f]{64}',path.read_bytes()) is None,str(path)
print(json.dumps(dict(provider_calls=0,actual_codex_sandbox=True,managed_status=result['status'],call_id=call,
 host_originals_unreadable=True,sibling_evidence_unreadable=True,task_science_readable=True,science_directory_has_no_control_receipts=True,backend_permissions=backend)))
'''


@pytest.mark.parametrize('network_enabled',[False,True],ids=['configured-network-denied','configured-network-allowed'])
def test_actual_nested_codex_sandbox_uses_host_bridge(tmp_path, execution_registry, monkeypatch, network_enabled):
    """Real outer bwrap + installed inner sandbox + ordinary files; zero provider calls.

    The sole helper substitution points the test's codex-linux-sandbox alias at
    the actual installed native, while the outer native protocol is a fixture.
    A separate new allowed-network fixture changes its own named profile before
    first configuration. Neither accepted configurations nor production defaults
    change. No namespace, managed execution, or original verifier is mocked.
    """
    import os
    import shutil
    import subprocess
    import sys
    from scidiscovery.research_loop import budget,controller,native
    from test_author_closeout_e2e import source_python
    monkeypatch.delenv('THIN_HWA_AUTH_SOURCE',raising=False)
    if shutil.which('bwrap') is None:pytest.skip('Actual bwrap is unavailable')
    entry=shutil.which('codex')
    package=Path(entry).resolve().parent.parent if entry else Path('/unavailable')
    vendor=package/'node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl'
    actual=Path(os.environ.get('SCID_TEST_CODEX_NATIVE',str(vendor/'bin/codex')))
    if not actual.is_file():pytest.skip('Explicit installed Codex native is unavailable')
    repo=Path(__file__).resolve().parents[2];launcher=repo/'scripts/acceptance/rootless_cov_launcher.py'
    python,scid_python,runtime_paths=source_python(tmp_path,repo)
    bootstrap='import runpy,sys;runpy.run_path(sys.argv.pop(1),run_name="__main__")'
    comparison=tmp_path/'.trial/current-task';comparison.mkdir(parents=True)
    sibling=tmp_path/'.trial/sibling-run';sibling.mkdir()
    c.write(sibling/'A-conclusion.json',dict(conclusion='unrelated comparison canary'))
    c.write(sibling/'score.json',dict(score='unrelated private scoring canary'))
    public=comparison/'public';harness=public/'environment/harness';harness.mkdir(parents=True)
    (public/'instruction.md').write_text('Engineering namespace and transport fixture only; no scientific qualification.')
    (public/'task.toml').write_text('fixture=true\n');(public/'environment/Dockerfile').write_text('FROM fixture\n')
    (harness/'exp_cli.py').write_text(PUBLIC_CLI);(harness/'runner.py').write_text(PUBLIC_RUNNER)
    for name in ('train.csv','val.csv','meta.json'):(harness/name).write_text('{}')
    manifest=tmp_path/'public-manifest.json'
    c.write(manifest,dict(task_revision='d78f5eb52ad754c5ee9154741af73130a85a65b8',
        public_files_sha256={p.relative_to(public).as_posix():c.digest(p) for p in public.rglob('*') if p.is_file()}))
    profile_name='namespace-network-allowed' if network_enabled else 'namespace-no-model'
    network_section=f'[permissions.{profile_name}.network]\nenabled=false'
    host_wrapper=tmp_path/'host-launcher.py'
    host_wrapper.write_text('import importlib.util\nfrom pathlib import Path\n'
        f'spec=importlib.util.spec_from_file_location("actual_launcher",{str(launcher)!r})\n'
        'module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)\n'
        'original=module.prepare_home\n'
        'def prepare(s,w,h,auth_source=None):\n'
        ' original(s,w,h,auth_source)\n'
        + (f' config=h/"config.toml";text=config.read_text();old={network_section!r}\n'
            ' assert text.count(old)==1;config.write_text(text.replace(old,old.replace("enabled=false","enabled=true")))\n' if network_enabled else '') +
        ' alias=Path(s["scene_root"])/"host-runtime/native-bin/codex-linux-sandbox"\n'
        f' alias.unlink();alias.symlink_to({str(actual)!r})\n'
        'module.prepare_home=prepare\nmodule.main()\n')
    fake=tmp_path/'fake-native'
    fake.write_text('#!'+sys.executable+'\nimport json,os,pathlib,subprocess,sys\n'
        'args=sys.argv[1:];sys.stdin.read();work=pathlib.Path(args[args.index("-C")+1])\n'
        'checker=work/"namespace-check.py"\n'
        f'checker.write_text({NAMESPACE_CHECKER!r})\n'
        'inner=pathlib.Path("/task-tmp/inner-runtime");inner.mkdir(mode=0o700);os.chmod("/task-tmp",0o700)\n'
        'os.environ.update(TMPDIR=str(inner),XDG_RUNTIME_DIR=str(inner))\n'
        f'command=[{str(actual)!r},"sandbox","-P",{profile_name!r},"-C",str(work),"--",{scid_python!r},"-B","-I","-c",{bootstrap!r},str(checker)]\n'
        'reply=subprocess.run(command,capture_output=True,text=True)\n'
        'assert reply.returncode==0,(reply.stdout,reply.stderr)\n'
        '(work/"artifacts/namespace-check.json").write_text(reply.stdout)\n'
        'print(json.dumps(dict(type="thread.started",thread_id="11111111-1111-4111-8111-111111111111")),flush=True)\n'
        'print(json.dumps(dict(type="turn.completed",usage=dict(input_tokens=1,cached_input_tokens=0,output_tokens=0))),flush=True)\n'
        'payload=dict(summary="Engineering transport fixture",scientific_result="No scientific efficacy measured.",limitations=["No model/provider/TCAD run."],implementation_selection=None,stage_intent="review",mechanism_findings=[dict(mechanism="Fixture mechanism",basis="observed",finding="The inert process transport completed; no scientific efficacy was measured.")])\n'
        'task=json.loads((work/"science-task.json").read_text())\n'
        'if task.get("candidate_nomination_mechanisms"):\n'
        ' payload["candidate_nominations"]=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row["mechanism_ref"],reason="This inert transport fixture declares no scientifically suitable candidate.") for row in task["candidate_nomination_mechanisms"] if row["status"]!="waived"])\n'
        'pathlib.Path(args[args.index("--output-last-message")+1]).write_text(json.dumps(payload))\n')
    fake.chmod(0o555)
    root=comparison/'instance';operator=comparison/'operator.json'
    c.write(operator,dict(scene_root=str(root),public_root=str(public),public_manifest=str(manifest),runs_dir=str(root/'runs'),
        submission_dir=str(root/'submission'),native=str(fake),native_sha256=c.digest(fake),
        code_mode_host=dict(path=str(actual.parent/'codex-code-mode-host'),sha256=c.digest(actual.parent/'codex-code-mode-host')),
        task_python=python,scid_python=scid_python,model='no-model',reasoning_effort='high',profile=profile_name,
        runtime_paths=list(dict.fromkeys([*runtime_paths,str(actual.parent.parent)])),
        cov_engineering_check_reserve_seconds=0))
    controller.prepare(root,public/'instruction.md',public,[scid_python,'-B','-I','-c',bootstrap,str(host_wrapper),'--config',str(operator),'--'],
        max_tokens=10000,max_seconds=120,max_steps=4,model='no-model',reasoning_effort='high',experiment_mode='external',
        node_budget_policy={role:dict(max_observed_tokens=1000,max_seconds=90) for role in 'HWAE'})
    c.write(root/'host-started.json',budget.clock_stamp());budget.transition(root,'advance','Zero-provider actual namespace check')
    from test_history_handoff import finish
    finish(root,1,'H',[],action='experiment',search_choice=dict(scope='Engineering transport only',
        commitments=[dict(label='Fixture mechanism',minimal_test='One inert process through the declared namespace',existing_ref=None)],
        focus=dict(label='Fixture mechanism',action='screen')))
    c.build_job(root,2,'W',[1])
    (c.workspace(root,2)/'work/task-science.txt').write_text('this task scientific input remains readable')
    import socket
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as host_listener:
        host_listener.bind(('127.0.0.1',0));host_listener.listen(2)
        address=host_listener.getsockname()
        with socket.create_connection(address,timeout=1):pass
        c.write(c.workspace(root,2)/'work/network-probe.json',dict(port=address[1],host_netns=os.stat('/proc/self/ns/net').st_ino,network_enabled=network_enabled))
        native.execute_native(root,2)
    work=c.workspace(root,2);proof=c.read(work/'artifacts/namespace-check.json')
    assert proof['provider_calls']==0 and proof['actual_codex_sandbox'] is True
    assert proof['managed_status']=='known_success' and proof['host_originals_unreadable'] is True
    assert all(proof['backend_permissions'].values())
    assert proof['sibling_evidence_unreadable'] and proof['task_science_readable']
    config=c.read(execution.origin(root,2)/'experiment-config.json')
    accepted=e.Records(config).accepted(proof['call_id'])
    assert accepted['prepared']['sha256']==c.digest(work/'inert-source.py')
    assert not (work/'work/lineage/calls').exists()
