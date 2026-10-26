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


pytestmark = pytest.mark.process_e2e


def _code_payload(work, *, name='source.json', data=b'{}'):
    directory = work / 'code'
    directory.mkdir(exist_ok=True)
    source = directory / name
    source.write_bytes(data)
    return source


def invoke(capsys, path, *arguments, expected=0, diagnostics=None):
    code = e.main(['--config', str(path), *map(str, arguments)])
    captured = capsys.readouterr()
    if code != expected and diagnostics:
        pytest.fail('\n'.join(diagnostics) + '\nPublic response: ' + captured.out, pytrace=False)
    assert code == expected, dict(code=code, expected=expected, public_response=captured.out,
                                 host_diagnostics=diagnostics)
    value = json.loads(captured.out)
    # Test fixtures use UUID operation handles; no mechanical checksum belongs
    # in these framework-produced response objects.
    assert re.search(r'[0-9a-f]{64}', captured.out) is None
    return value


def test_real_main_client_contracts_and_retained_outputs(tmp_path, monkeypatch, remote_registry, capsys):
    import traceback
    from functools import partial
    diagnostics=[]
    diagnostic=e._diagnostic
    def record_diagnostic(error, fallback, *, config=None):
        diagnostics.append(''.join(traceback.format_exception(type(error),error,error.__traceback__)))
        return diagnostic(error,fallback,config=config)
    monkeypatch.setattr(e,'_diagnostic',record_diagnostic)
    root, job, config, _ = case(tmp_path, monkeypatch)
    work = c.workspace(root, 2)
    path = work / 'work/experiment-config.json'
    run_cli = partial(invoke,capsys,path,diagnostics=diagnostics)
    assert c.read(path) == dict(worker_view_version=1, step=2, attempt=0)
    assert c.read(execution.origin(root, 2) / 'experiment-config.json') == config
    assert not (work/'work/lineage/calls').exists()
    assert not Path(config['record_dir']).is_relative_to(work)
    assert 'config_record' in config['execution_interface']
    capabilities = run_cli('capabilities')
    assert capabilities['contracts'][0]['key'] == 'opaque_input'
    assert 'sha256' not in capabilities['contracts'][0]
    for metadata in (False, True):
        target = work / 'work' / ('metadata.json' if metadata else 'contract.json')
        result = run_cli('describe', '--key', 'opaque_input', '--output', target,
            *(['--metadata'] if metadata else []))
        assert set(result['contract']) == {'path', 'size_bytes'}
        assert 'sha256' not in c.read(target)
    # A legitimate scientist original can implement/use a hash. View handling
    # must preserve its bytes rather than recursively scrub scientific data.
    original = b'{"algorithm":"sha256","sha256":"' + b'a' * 64 + b'"}'
    source = _code_payload(work, name='scientific-source.json', data=original)
    result = run_cli('run', '--payload', source, '--new-plan', '--mechanism', 'P')
    call = result['call_id']
    for command in ('query', 'lookup'):
        assert run_cli(command, '--call-id', call)['state'] == 'succeeded'
    collected = run_cli('collect', '--call-id', call)
    output = collected['outputs'][0]
    assert output['name'] == 'original' and output['retention'] == 'retained_original'
    assert Path(output['path']).is_relative_to(work)
    assert Path(output['path']).read_bytes() == original == source.read_bytes()
    assert run_cli('reconcile', '--call-id', call)['status'] == 'known_success'
    assert run_cli('lineage-status')['counts']['known_success'] == 1
    accepted = e.Records(config).accepted(call)
    assert accepted['source_script'] == str(source)
    assert accepted['code_version']['logical_root'] == 'code'
    assert accepted['code_version']['entry'] == source.name
    assert 'sha256' not in c.read(e.Records(config).directory(call) / 'execution-observation.json')['outputs'][0]



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
        else:value['task_ref'] = dict(path='/wrong/original.json')
        c.write(target, value)
    assert invoke(capsys, path, 'capabilities', expected=3)['status'] == 'integrity_error'
    assert not e.Records(config).calls()


def test_private_capability_comparison_still_rejects_change(tmp_path, monkeypatch, remote_registry, capsys):
    root, _, config, _ = case(tmp_path, monkeypatch)
    original = copy.deepcopy(config)
    config['execution_interface']['public_capabilities'][0]['schema_id'] = 'changed_schema'
    # Exercise exact frozen loading, not a standalone JSON cleanup helper.
    with pytest.raises(e.IntegrityError):e.load(config)
    assert original['execution_interface']['public_capabilities'][0]['schema_id'] != 'changed_schema'


def test_unknown_original_cli_does_not_replay(tmp_path, monkeypatch, remote_registry, capsys):
    root, _, config, _ = case(tmp_path, monkeypatch, outcome='unknown')
    work = c.workspace(root, 2);path = work / 'work/experiment-config.json'
    source = _code_payload(work)
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
        source=_code_payload(work)
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
    source=_code_payload(work)
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


@pytest.mark.parametrize('mutation',['accepted.json','completed.json','code_version'])
def test_cached_submission_revalidates_canonical_originals(tmp_path,monkeypatch,remote_registry,mutation):
    from scidiscovery.research_loop import worker_execution_bridge as bridge
    root,_,config,_=case(tmp_path,monkeypatch)
    work=c.workspace(root,2);slot=work/bridge.SUBMISSION_DIRECTORY
    source=_code_payload(work)
    def no_local_launch(*args):raise AssertionError('Remote fixture has no local process')
    with bridge.serve(slot,work/'work/experiment-config.json',work,launch=no_local_launch):
        result=bridge.client(slot,['run','--payload',str(source),'--new-plan','--mechanism','P'])
        assert result['result']['status']=='known_success'
        original=(slot/'submit.json').read_bytes()
        reference=json.loads(original)['submission_ref']
        assert bridge.client(slot,['capabilities'])['code']==0
        directory=e.Records(config).directory(result['result']['call_id'])
        target=directory/('accepted.json' if mutation=='code_version' else mutation)
        target.chmod(0o600)
        if mutation=='code_version':
            accepted=c.read(target)
            assert accepted['code_version']['entry']==source.name
            accepted['code_version']['entry']='unbound.json'
            c.write(target,accepted)
        else:target.write_bytes(target.read_bytes()+b' ')
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
    # Install the barrier before the isolated receiver is forked. Its actual
    # observation/release are files shared with the client, not thread memory.
    entered=tmp_path/'receiver-entered';release=tmp_path/'receiver-release';execute=bridge._execute
    def paused(server,arguments):
        import time
        entered.touch();deadline=time.monotonic()+5
        while not release.exists():
            if time.monotonic()>=deadline:raise AssertionError('Test scheduling gate timed out')
            time.sleep(.01)
        return execute(server,arguments)
    monkeypatch.setattr(bridge,'_execute',paused)
    with bridge.serve(slot,work/'work/experiment-config.json',work,launch=no_local_launch):
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiting=pool.submit(bridge.client,slot,['capabilities'])
            import time
            deadline=time.monotonic()+3
            while not entered.exists() and time.monotonic()<deadline:time.sleep(.01)
            assert entered.exists()
            request=c.read(slot/'submit.json')
            request['arguments']=['lineage-status']
            c.write(slot/'submit.json',request)
            release.touch()
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
    source=_code_payload(work)
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
