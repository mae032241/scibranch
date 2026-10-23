"""Exact default-Local inputs; two tiny processes, synthetic native identities."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import uuid

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import (contracts as c, controller, execution_inputs as inputs,
    experiments as e, formal_review as review, history, native, qualification as q)
from scidiscovery.research_loop.local_experiments import LocalExecution
from test_history_handoff import finish


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
    code='from pathlib import Path\nPath("artifacts/value.txt").write_text(Path("value.txt").read_text())\n'
    finish(root,1,'H',[],action='experiment',files={'base/run.py':code,'base/value.txt':'42'})
    job=c.build_job(root,2,'W',[1]);work=c.workspace(root,2);record=c.host_record(work)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/002.json')))
    path=work/'work/experiment-config.json'
    config=e.configure(root,2,'local_execution:command',options,path)
    return root,job,config,path


def authored(root):
    directory=c.workspace(root,2)/'experiments/revision';directory.mkdir(parents=True)
    script=directory/'run.py';script.write_text('from pathlib import Path\nPath("artifacts/value.txt").write_text("43")\n')
    return directory,script


@pytest.mark.process_e2e
def test_public_local_inputs_authoring_and_unselected_contributors_reach_exact_review(local_scene,capsys):
    root,job,config,path=local_scene
    assert job.get('selected_inputs',[])==[]
    bound=c.workspace(root,2)/'inputs/parents/001/artifacts/base'
    assert e.main(['--config',str(path),'run','--experiment-dir',str(bound),'--script',str(bound/'run.py')])==0
    first=json.loads(capsys.readouterr().out)
    assert first['status']=='known_success',first
    directory,script=authored(root)
    assert e.main(['--config',str(path),'run','--experiment-dir',str(directory),'--script',str(script)])==0
    second=json.loads(capsys.readouterr().out)
    assert second['status']=='known_success',second
    records=e.Records(config);first_accepted=records.accepted(first['call_id']);second_accepted=records.accepted(second['call_id'])
    assert {row['origin']['kind'] for row in first_accepted['input_sources']['view']['files']}=={'producer'}
    assert {row['origin']['kind'] for row in second_accepted['input_sources']['view']['files']}=={'author'}
    # New authoring bytes are snapshotted; later work does not rewrite the call.
    script.write_text('raise RuntimeError("later edit, never executed")\n')
    finish(root,2,'W',[1],files={'revised.py':'def predict(): return 43\n'})
    record=c.host_record(c.workspace(root,2));seal=c.read(record/'result-identity.json')
    proof=q._read(root,seal['source_provenance'])
    assert len(proof['managed_input_coverage'])==2 and all(row['complete'] for row in proof['managed_input_coverage'])
    assert q.output_status(root,2)['restriction']=='clear'
    authors=review.authors(root,2)
    assert authors['complete'] and set(map(tuple,review.authors(root,1)['participants']))<=set(map(tuple,authors['participants']))
    selecting=c.build_job(root,3,'A',[2]);material=next(item for item in seal['materials'] if item['source']==str(c.workspace(root,2)/'artifacts/revised.py'))
    choices=[item['id'] for item in history.implementation_options(root,2) if item['label']=='revised.py'
        and history.resolve_implementation(root,item['id'],selecting['history']['nodes'])==material]
    assert len(choices)==1,choices
    requested,_,request_record=finish(root,3,'A',[2],action='formal_review',selection=choices)
    role,parents,instruction,back=review.request_route(root,requested,c.read(request_record/'result.json'),[3])
    assert back['kind']=='formal_review'
    assert q._read(root,back['request'])['subject']['material']==material
    c.build_job(root,4,role,parents,instruction,back);review_record=c.host_record(c.workspace(root,4))
    c.write(review_record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/004.json')))
    c.write(review_record/'science-output.json',dict(verdict='pass',summary='Exact Local fixture',scientific_result='Declared input/author coverage only.',limitations=['Synthetic native identities; two tiny local processes.']))
    c.write(review_record/'usage.json',dict(input_tokens=1,cached_input_tokens=0,output_tokens=1))
    c.write(review_record/'native-outcome.json',dict(returncode=0,stop_reason=None,usage=native.usage(root),raw_ref=c.file_ref(review_record/'science-output.json')))
    c.seal_result(root,4)
    verdict=q._read(root,c.read(review_record/'result-identity.json')['formal_review'])
    assert verdict['status']=='passed' and verdict['independence']=='independent' and verdict['subject']['material']==material
    # Exact origin and full inventory, not matching bytes or an author self-label.
    changed=deepcopy(first_accepted);changed['input_sources']['view']['files'][0]['origin']['original']['producer_step']=2
    with pytest.raises(q.IntegrityError,match='frozen exact origin'):
        inputs.validate_local_input_sources(root,job,changed)
    changed=deepcopy(first_accepted);changed['input_sources']['view']['files'].pop()
    with pytest.raises(q.IntegrityError,match='snapshot inventory'):
        inputs.validate_local_input_sources(root,job,changed)
    # A retained input change invalidates the published coverage.
    saved=(record/'provenance.json').read_bytes()
    target=records.directory(second['call_id'])/'inputs/run.py';target.chmod(0o600);target.write_text('changed retained input\n')
    with pytest.raises(q.IntegrityError,match='identity changed'):
        q.validate_outputs(root,job,seal)
    assert (record/'provenance.json').read_bytes()==saved


def test_local_source_contract_unknown_originals_and_unknown_execution_do_not_upgrade(local_scene,tmp_path,monkeypatch):
    root,job,config,path=local_scene
    directory,script=authored(root)
    monkeypatch.setattr(LocalExecution,'_execute',lambda *args:None)  # No process/terminal; acceptance only.
    result=e.dispatch(config,script,command_options=dict(experiment_directory=str(directory)))
    assert result['status']=='unknown'
    accepted=e.Records(config).accepted(result['call_id'])
    assert inputs.validate_local_input_sources(root,job,accepted)==[]
    rows=history.managed_calls(root,job);coverage=history.managed_input_coverage(root,job,rows)
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
