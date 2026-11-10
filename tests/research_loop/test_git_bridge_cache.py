"""Fresh observation/collect/cache composition; no backend, Git or process runs."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from execution_fixtures import execution_registry
from test_mutable_run_inputs import (fixed_code,fragment,live_fragment,terminal_code,
    _configuration,_code,_prepare)
from scidiscovery.research_loop import (budget,code_versions,contracts as c,experiments as e,
    worker_execution_bridge as bridge)


def _saved(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(e.encoded(value))
    return e.proof(path)


def _replaced(path,raw):
    replacement=path.with_name(path.name+'.replacement');replacement.write_bytes(raw);replacement.replace(path)


def _cached_case(terminal_code,fragment,monkeypatch,*,command='query',terminal=True,omitted=False):
    root,job,record=fragment
    if terminal:
        row=terminal_code();config=row['config'];accepted=row['accepted'];directory=row['directory']
        observed=e.record_observation(config,row['adapter'],accepted['call_id'])
        assert observed['source_code_version']==accepted['code_version'] and observed['scientific_source'] is None
        assert {item['name'] for item in observed['scientific_outputs']}=={
            'result.txt','execution-logs/stdout.txt','execution-logs/stderr.txt'}
        collected=e._collected_view(config,accepted['call_id'],observed,row['adapter'])
        assert {item['name'] for item in collected['outputs']}=={item['name'] for item in observed['scientific_outputs']}
        assert all(Path(item['path']).is_relative_to(c.workspace(root,job['step'])/'work/retained-experiments')
            for item in collected['outputs'])
        assert next(Path(item['path']).read_bytes() for item in collected['outputs']
            if item['name']=='execution-logs/stdout.txt')==b'fixture stdout\n'
        draft=Path(accepted['source_script'])
    else:
        config=_configuration(root,job);draft=_code(root,job);_,accepted=_prepare(config,draft)
        directory=e.Records(config).directory(accepted['call_id']);observed=None;collected=None
        e.Records(config).bind_external(accepted['call_id'],accepted['call_id'],'running')
    work=c.workspace(root,job['step']);call_id=accepted['call_id']
    result=(deepcopy(collected) if command=='collect' else dict(status='known_success' if terminal else 'running',call_id=call_id))
    skipped=None
    if command=='collect' and omitted:
        skipped=result['outputs'].pop();result['outputs_omitted_count']=1
    arguments=([command,'--script',str(draft),'--new-plan'] if command=='run' else [command,'--call-id',call_id])
    request=dict(submission_ref='S001',arguments=arguments);raw=bridge._encoded(request)
    claims=root/'host-runtime/worker-submissions';claim=claims/'S001';claim.mkdir(parents=True)
    public=work/bridge.SUBMISSION_DIRECTORY;public.mkdir(parents=True,exist_ok=True)
    (claim/'submit.json').write_bytes(raw)
    config_path=work/'work/version-config.json';client=c.read(config_path)
    assert client['step']==job['step'] and client['attempt']==0
    _saved(claim/'binding.json',dict(step=client['step'],attempt=client['attempt'],configuration=config,
        retained_at_start=False,submission=e.proof(claim/'submit.json')))
    response=dict(code=0 if terminal else 2,result=result,submission_ref='S001',request=request)
    (claim/'response.json').write_bytes(bridge._encoded(response))
    calls=[bridge._call_evidence(config,call_id)]
    assert bridge._verify_calls(config,calls,arguments=arguments,response=response)==terminal
    _saved(claim/'outcome.json',dict(submission=e.proof(claim/'binding.json'),
        response=e.proof(claim/'response.json'),calls=calls))
    execution=[]
    def forbidden(*args,**kwargs):pytest.fail('A cache read consumed Git, built a backend, or replayed the call')
    def execute(*args,**kwargs):execution.append((args,kwargs));forbidden()
    monkeypatch.setattr(bridge,'_execute',execute)
    monkeypatch.setattr(e,'load',forbidden)
    for name in ('verify','materialize','capture'):monkeypatch.setattr(code_versions,name,forbidden)
    server=SimpleNamespace(work=work,directory=public,config_path=config_path,configuration=config,client=client,
        claims=claims,diagnostic=root/'cache-diagnostic.json')
    originals={path:path.read_bytes() for path in [claim/name for name in (
        'submit.json','binding.json','response.json','outcome.json')]}
    return SimpleNamespace(root=root,config=config,directory=directory,accepted=accepted,observation=observed,
        collected=collected,skipped=skipped,draft=draft,server=server,raw=raw,claim=claim,response=response,
        originals=originals,execution=execution,record=record)


def _reply(case):
    bridge._handle(case.server,case.raw)
    return c.read(case.server.directory/'response.json')


@pytest.mark.parametrize('command',['run','query'])
def test_cached_terminal_preserves_fresh_projection_without_code_or_backend(
        terminal_code,fragment,monkeypatch,command):
    case=_cached_case(terminal_code,fragment,monkeypatch,command=command)
    assert not Path(case.accepted['script']['path']).exists()
    _replaced(case.draft,b'changed mutable draft')
    result=_reply(case)
    assert result['code']==0 and result['result']['status']=='known_success'
    assert result['result']['call_id']==case.accepted['call_id'] and case.execution==[]
    assert all(path.read_bytes()==raw for path,raw in case.originals.items())


def test_cached_collect_reuses_fresh_retained_outputs_and_logs(terminal_code,fragment,monkeypatch):
    case=_cached_case(terminal_code,fragment,monkeypatch,command='collect');case.draft.unlink()
    result=_reply(case)
    assert result['code']==0 and result['result']['status']=='collected'
    assert result['result']['outputs']==case.collected['outputs'] and case.execution==[]
    assert all(path.read_bytes()==raw for path,raw in case.originals.items())


def test_cached_collect_rejects_replaced_returned_log_without_reexecution(terminal_code,fragment,monkeypatch):
    case=_cached_case(terminal_code,fragment,monkeypatch,command='collect')
    assert _reply(case)['result']['status']=='collected'
    output=next(item for item in case.collected['outputs'] if item['name']=='execution-logs/stdout.txt')
    path=Path(output['path']);_replaced(path,path.read_bytes())
    result=_reply(case)
    assert result['code']==3 and result['result']['status']=='integrity_error' and case.execution==[]
    assert all(path.read_bytes()==raw for path,raw in case.originals.items())


def test_cached_collect_does_not_consume_unreturned_retained_output(terminal_code,fragment,monkeypatch):
    case=_cached_case(terminal_code,fragment,monkeypatch,command='collect',omitted=True)
    assert _reply(case)['result']['status']=='collected'
    original=next(item for item in case.observation['scientific_outputs'] if item['name']==case.skipped['name'])
    for path in {Path(case.skipped['path']),Path(original['path'])}:path.unlink()
    result=_reply(case)
    assert result['code']==0 and result['result']['outputs']==case.response['result']['outputs']
    assert result['result']['outputs_omitted_count']==1 and case.execution==[]


@pytest.mark.parametrize('fault',['accepted','process','terminal','binding','response','outcome'])
def test_cached_control_rejects_changed_mandatory_originals(terminal_code,fragment,monkeypatch,fault):
    case=_cached_case(terminal_code,fragment,monkeypatch)
    assert _reply(case)['result']['status']=='known_success'
    paths=dict(accepted=case.directory/'accepted.json',process=case.directory/'process.json',
        terminal=case.directory/'terminal.json',binding=case.claim/'binding.json',
        response=case.claim/'response.json',outcome=case.claim/'outcome.json')
    path=paths[fault];_replaced(path,b'{"changed_original":true}\n' if fault=='outcome' else path.read_bytes())
    result=_reply(case)
    assert result['code']==3 and result['result']['status']=='integrity_error' and case.execution==[]
    assert all(item.read_bytes()==raw for item,raw in case.originals.items() if item!=path)


def test_cached_unknown_keeps_same_calls_and_consumed_budget(terminal_code,fragment,fixed_code,monkeypatch):
    case=_cached_case(terminal_code,fragment,monkeypatch,terminal=False)
    accepted=case.accepted;root=case.root
    # An unavailable temporary view must not turn this unknown cached call into
    # a second execution or a refund.
    fixed_code['exports'][(root,1,accepted['call_id'])].joinpath(accepted['code_version']['entry']).unlink()
    case.draft.unlink()
    stamp=budget.clock_stamp();before=budget.snapshot(root,sample_stamp=stamp)
    # This owner is live: absence of a terminal usage original remains unknown,
    # and cache reads must preserve that absence as well as accepted spending.
    usage=case.record/'usage.json'
    assert not usage.exists()
    calls=[path.name for path in e.Records(case.config).calls()]
    for _ in range(2):
        result=_reply(case)
        assert result['code']==2 and result['result']['status']=='unknown'
        assert result['result']['call_id']==accepted['call_id']
    assert budget.snapshot(root,sample_stamp=stamp)==before and not usage.exists()
    assert [path.name for path in e.Records(case.config).calls()]==calls and case.execution==[]
    assert not (case.directory/'execution-observation.json').exists()
    assert all(path.read_bytes()==raw for path,raw in case.originals.items())


def test_bridge_publish_syncs_file_then_replace_then_parent(tmp_path, monkeypatch):
    import os
    import stat
    events = []
    fsync, replace = os.fsync, os.replace
    def sync(descriptor):
        events.append('directory_sync' if stat.S_ISDIR(os.fstat(descriptor).st_mode) else 'file_sync')
        fsync(descriptor)
    def publish(source, destination):
        events.append('replace')
        replace(source, destination)
    monkeypatch.setattr(bridge.os, 'fsync', sync)
    monkeypatch.setattr(bridge.os, 'replace', publish)
    destination = tmp_path / 'response.json'
    bridge._publish(destination, b'{"status":"unknown"}\n')
    assert destination.read_bytes() == b'{"status":"unknown"}\n'
    assert events == ['file_sync', 'replace', 'directory_sync']


@pytest.mark.parametrize('receipt', ['missing', 'unknown', 'different_process'])
def test_stopped_bridge_requires_positive_exact_cleanup(tmp_path, monkeypatch, receipt):
    from scidiscovery.research_loop import runtime
    identity = dict(path='jobs/001.json')
    process = dict(pid=123, boot_id='original-boot', start_ticks=1, pgid=123, sid=123, task_ref=identity)
    c.write(tmp_path / 'bridge-process.json', process)
    if receipt != 'missing':
        c.write(tmp_path / 'bridge-outcome.json', dict(
            process=dict(process, start_ticks=2) if receipt == 'different_process' else process,
            cleanup='cleanup_unknown' if receipt == 'unknown' else 'observed_group_cleaned'))
    monkeypatch.setattr(runtime, 'process_matches', lambda *args: False)
    with pytest.raises(ValueError, match='cleanup is unconfirmed'):
        bridge.stopped(tmp_path, identity)
