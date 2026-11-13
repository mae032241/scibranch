"""Plugin v2 exact original relations; no models and no default-view rewrite."""
import json
import os
from pathlib import Path
import sys

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, execution, history, lineage, experiments
from cov_execution import lineage as cov
from test_history_handoff import finish
from test_nonlocal_execution_observation import research


@pytest.fixture(autouse=True)
def synthetic_host_launch(tmp_path,monkeypatch):
    monkeypatch.setenv('EXP_RUN_DIR',str(tmp_path/'instance/runs'))
    monkeypatch.setenv('EXP_RUNNER',str(tmp_path/'runner.py'))
    from scidiscovery.research_loop import worker_execution_bridge as bridge
    # Exercise real call/observer processes with a synthetic trusted host seam.
    token=bridge._LOCAL_LAUNCH.set(lambda config,accepted,command,cwd:(command,cwd,None))
    try:
        yield
    finally:
        bridge._LOCAL_LAUNCH.reset(token)


def scene(tmp_path):
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('Test exact scientific lineage mechanics')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['fake'],max_tokens=10000,max_seconds=120,max_steps=8,
        model='no-model',reasoning_effort='high',search_policy=None,
        history_observer=dict(adapter=str(Path(cov.__file__).resolve()),contract_version=2,
            config=dict(runs='runs',calls='project/nodes',evaluation_key='covshift-public-val-v1')))
    finish(root,1,'H',[],action='experiment')
    c.build_job(root,2,'W',[1]);record=c.host_record(c.workspace(root,2))
    intent=dict(task_ref=c.record_ref(root/'jobs/002.json'))
    if c.settings(root).get('node_budget_policy') is not None:intent['budget_slice']=budget.accept_node(root,c.read(root/'jobs/002.json'))
    (c.workspace(root,2)/'code').mkdir(exist_ok=True)
    c.write(record/'native-intent.json',intent)
    accepted_budget.accept_native(root,2,record,intent)
    from test_cov_receipt import CLI, RUNNER
    backend=tmp_path/'exp.py';backend.write_text(CLI)
    (tmp_path/'runner.py').write_text(RUNNER)
    output=tmp_path/'config.json'
    cov.configure(root,2,[sys.executable,str(backend)],root/'runs',output,receipt_channel='public_runner_main')
    config=json.loads(experiments.read(output));config_ref=dict(path=str(output))
    return root,config,config_ref


def _failure_originals(config,result):
    """Retain only this failed call's named originals; never enumerate records."""
    diagnostic=dict(result=result,config=config,originals={})
    call=result.get('call_id')
    if (not isinstance(call,str) or not call or len(call)>128 or '..' in call
            or any(not (letter.isalnum() or letter in '-_.') for letter in call)):
        return json.dumps(diagnostic,ensure_ascii=True)
    directory=Path(config['record_dir'])/'calls'/call
    def original(path,limit):
        try:
            with path.open('rb') as stream:data=stream.read(limit+1)
            diagnostic['originals'][str(path)]=dict(text=data[:limit].decode('utf-8',errors='replace'),
                truncated=len(data)>limit)
            return data[:limit] if len(data)<=limit else None
        except OSError as error:
            diagnostic['originals'][str(path)]=dict(error=str(error))
            return None
    data=original(directory/'completed.json',65536)
    if data is not None:
        try:
            completed=json.loads(data)
            references={}
            for reference in completed.get('proofs',[]):
                path=Path(reference['path'])
                if path.parent==directory and path.name in {'stdout.raw','stderr.raw'}:
                    references[path.name]=path
            for name in ('stdout.raw','stderr.raw'):
                if name in references:original(references[name],16384)
        except (ValueError,KeyError,TypeError,AttributeError) as error:
            diagnostic['completed_error']=str(error)
    text=json.dumps(diagnostic,ensure_ascii=True,indent=2)+'\n'
    destination=os.environ.get('SCID_TEST_LOG_DIR')
    if destination:
        try:
            (Path(destination)/('lineage-'+call+'.json')).write_text(text)
        except OSError:
            pass  # Diagnostic publication must not replace the failed assertion.
    return text


def test_v1_existing_contract_bytes_and_relations_remain_unknown(tmp_path):
    public=tmp_path/'public';public.mkdir();objective=public/'objective';objective.write_text('Legacy science')
    root=tmp_path/'instance';controller.prepare(root,objective,public,['fake'],max_tokens=1000,max_seconds=60,max_steps=4,
        model='fake',reasoning_effort='high',search_policy=None)
    finish(root,1,'H',[],action='wait')
    original={p:p.read_bytes() for p in (root/'jobs/001.json',c.workspace(root,1)/'science-task.json',execution.origin(root,1)/'result-identity.json')}
    assert c.validate(c.workspace(root,1))['next_action']=='wait'
    assert lineage.build(root,2)['relations']==[]
    assert all(p.read_bytes()==data for p,data in original.items())
    assert 'contract_version' not in history.observer_config(dict(adapter=str(Path(cov.__file__).resolve()),config={}))


@pytest.mark.process_e2e
@pytest.mark.parametrize('version', [1,2])
def test_integrity_sentinel_is_v2_only_and_preserves_v1_optional_failure(tmp_path,version):
    public=tmp_path/'public';public.mkdir();objective=public/'objective';objective.write_text('Versioned observation failures')
    adapter=tmp_path/'observer.py';adapter.write_text('print(\'{"integrity_error":"fixture original changed"}\')\n')
    descriptor=dict(adapter=str(adapter),config={})
    if version==2:descriptor['contract_version']=2
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['fake'],max_tokens=1000,max_seconds=60,max_steps=4,
        model='fake',reasoning_effort='high',search_policy=None,history_observer=descriptor)
    if version==2:
        with pytest.raises(history.ObserverIntegrityError):finish(root,1,'H',[],action='wait')
        assert not (execution.origin(root,1)/'result-identity.json').exists()
    else:
        finish(root,1,'H',[],action='wait')
        seal=c.read(execution.origin(root,1)/'result-identity.json')
        assert c.read(root/seal['observations']['path'])['coverage']['status']=='unavailable'
        assert c.validate(c.workspace(root,1))['next_action']=='wait'
