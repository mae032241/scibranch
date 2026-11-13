"""Exact current-W Local code; two tiny processes, synthetic native identities."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import uuid

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import (accepted_budget, contracts as c, controller, execution, execution_inputs as inputs,
    experiment_client, experiments as e, formal_review as review, history, native, originals, qualification as q)
from scidiscovery.research_loop.local_experiments import LocalExecution
from scidiscovery.research_loop import worker_execution_bridge as bridge
from test_history_handoff import finish
from test_nonlocal_execution_observation import research, research_path


@pytest.fixture
def local_scene(tmp_path, monkeypatch, execution_registry):
    public=tmp_path/'public';public.mkdir();goal=public/'objective.md';goal.write_text('Use the exact supplied experiment and report one revised result.')
    launcher=tmp_path/'codex';launcher.write_text('# Native identity fixture only; never executed\n');launcher.chmod(0o700)
    root=tmp_path/'instance'
    options=dict(run_argv=[sys.executable],max_seconds=5)
    controller.prepare(root,goal,public,[str(launcher)],max_tokens=4000,max_seconds=120,max_steps=5,
        model='fixture',reasoning_effort='high',search_policy=None,experiment_mode='local',node_budget_mode='soft',
        execution_config=dict(executor='local_execution:command',configuration=options),
        formal_review_policy={'version':1},source_restrictions=[dict(source=str(goal),restriction='clear')])
    original_seal=c.seal_result

    def seal(root,step):
        work=c.workspace(root,step);record=c.host_record(work)
        thread=str(uuid.uuid5(uuid.NAMESPACE_URL,'local-input-source-'+str(step)))
        home=tmp_path/'identity';session=home/'sessions'/(thread+'.jsonl');session.parent.mkdir(parents=True,exist_ok=True)
        session.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
        (record/'native.jsonl').write_text(json.dumps(dict(type='thread.started',thread_id=thread))+'\n')
        outcome=c.read(record/'native-outcome.json');outcome.update(thread_id=thread,
            native_identity=dict(participant_scope='codex_exec_single_agent_v1',home=str(home),path=str(session)))
        c.write(record/'native-outcome.json',outcome)
        return original_seal(root,step)

    monkeypatch.setattr(c,'seal_result',seal)
    code='print("42")\n'
    finish(root,1,'H',[],action='experiment',files={'base/run.py':code,'base/value.txt':'42'})
    job=c.build_job(root,2,'W',[1]);work=c.workspace(root,2);record=c.host_record(work)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/002.json')))
    accepted_budget.accept_native(root,2,record,c.read(record/'native-intent.json'))
    path=work/'work/experiment-config.json'
    config=e.configure(root,2,'local_execution:command',options,path)
    return root,job,config,path


def authored(root):
    work=c.workspace(root,2);directory=work/'experiments/revision'
    script=work/'code/revision/run.py';script.parent.mkdir(parents=True);script.write_text('print("43")\n')
    return directory,script


@pytest.mark.process_e2e
def test_local_source_contract_unknown_originals_and_unknown_execution_do_not_upgrade(local_scene,tmp_path,monkeypatch):
    root,job,config,path=local_scene
    directory,script=authored(root)
    monkeypatch.setattr(LocalExecution,'_execute',lambda *args:None)  # No process/terminal; acceptance only.
    result=e.dispatch(config,script,mechanism=research(root,'1'),command_options=dict(experiment_directory=str(directory)))
    assert result['status']=='unknown'
    accepted=e.Records(config).accepted(result['call_id'])
    assert inputs.validate_local_input_sources(root,job,accepted)==[]
    rows=history._observe_managed_calls_from_facts(root,job,chain=execution.records(root,job['step']),scope=config['scope'])
    coverage=history._accept_managed_input_coverage(root,job,rows)
    assert len(coverage)==1 and coverage[0]['complete'] is False
    assert inputs.validate_local_input_sources(root,job,{key:value for key,value in accepted.items() if key!='input_sources'}) is None
    missing=deepcopy(accepted);missing['interface']['public_capabilities']=[]
    with pytest.raises(q.IntegrityError,match='unique frozen source capability'):
        inputs.validate_local_input_sources(root,job,missing)
    outside=tmp_path/'external';outside.mkdir();source=outside/'run.py';source.write_text('print("external")\n')
    descriptor=e.LocalFileDescriptor(name='outside',local_path=str(source),size_bytes=source.stat().st_size,media_type='text/x-python')
    adapter=e.load(config,path=path);metadata=adapter.acceptance_metadata(descriptor,command_options=dict(experiment_directory=str(outside)))
    view=adapter.input_sources(descriptor,metadata)
    assert view['files']==[dict(name='run.py',origin=dict(kind='unknown'))]
