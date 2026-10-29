"""Exact current-W Local code; two tiny processes, synthetic native identities."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import uuid

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import (contracts as c, controller, execution_inputs as inputs,
    experiment_client, experiments as e, formal_review as review, history, native, originals, qualification as q)
from scidiscovery.research_loop.local_experiments import LocalExecution
from scidiscovery.research_loop import worker_execution_bridge as bridge
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
    code='print("42")\n'
    finish(root,1,'H',[],action='experiment',files={'base/run.py':code,'base/value.txt':'42'})
    job=c.build_job(root,2,'W',[1]);work=c.workspace(root,2);record=c.host_record(work)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/002.json')))
    path=work/'work/experiment-config.json'
    config=e.configure(root,2,'local_execution:command',options,path)
    return root,job,config,path


def authored(root):
    work=c.workspace(root,2);directory=work/'experiments/revision'
    script=work/'code/revision/run.py';script.parent.mkdir(parents=True);script.write_text('print("43")\n')
    return directory,script


@pytest.mark.process_e2e
def test_public_local_code_and_exact_inputs_reach_independent_review(local_scene,capsys):
    root,job,config,path=local_scene
    assert job.get('selected_inputs',[])==[]
    work=c.workspace(root,2);bound=work/'inputs/parents/001/artifacts/base'
    producer_material=next(item for item in job['direct_materials']
        if item['path']==(bound/'value.txt').relative_to(work).as_posix())
    first_script=work/'code/base/run.py';first_script.parent.mkdir(parents=True)
    first_script.write_text('print("42")\n')
    def run(*arguments):
        code=e.main(['--config',str(path),'run',*map(str,arguments)])
        result=json.loads(capsys.readouterr().out);diagnostic=''
        if code and result.get('call_id'):
            stderr=e.Records(config).directory(result['call_id'])/'stderr.raw'
            if stderr.is_file():diagnostic=stderr.read_text()
        assert code==0,dict(result=result,stderr=diagnostic)
        assert result['status']=='known_success',result
        return result
    def launch(body,accepted,command,cwd):
        # Real inert processes consume exact sources; native namespace policy
        # is covered elsewhere and is not asserted by this provenance fixture.
        command=list(command)
        source=Path(accepted['script']['path'])
        assert source.is_file(), 'Exact accepted fixture source is unavailable'
        command[accepted['script_index']]=str(source)
        return command,cwd,None
    token=bridge._LOCAL_LAUNCH.set(launch)
    try:
        first=run('--experiment-dir',work/'experiments/base','--script',first_script)
        directory,script=authored(root)
        second=run('--experiment-dir',directory,'--script',script)
    finally:bridge._LOCAL_LAUNCH.reset(token)
    records=e.Records(config);first_accepted=records.accepted(first['call_id']);second_accepted=records.accepted(second['call_id'])
    # Git owns authored code. The Local view covers declared runtime snapshots;
    # neither inert program claims to consume the separately bound H inputs.
    for accepted,entry in ((first_accepted,'base/run.py'),(second_accepted,'revision/run.py')):
        assert accepted['input_sources']['view']['files']==accepted['snapshot_files']==[]
        assert accepted['code_version']['logical_root']=='code'
        assert accepted['code_version']['entry']==entry
        assert inputs.validate_local_input_sources(root,job,accepted)==[]
    # New authoring bytes are snapshotted; later work does not rewrite the call.
    script.write_text('raise RuntimeError("later edit, never executed")\n')
    finish(root,2,'W',[1],files={'revised.py':'def predict(): return 43\n'})
    record=c.host_record(c.workspace(root,2));seal=c.read(record/'result-identity.json')
    proof=q._read(root,seal['source_provenance'])
    assert len(proof['managed_input_coverage'])==2 and all(row['complete'] for row in proof['managed_input_coverage'])
    assert q.output_status(root,2)['restriction']=='clear'
    authors=review.authors(root,2)
    worker_session=c.read(record/'native-outcome.json')['thread_id']
    assert authors['complete'] and authors['participants']==[['codex_exec',worker_session]]
    selecting=c.build_job(root,3,'A',[2]);material=c._public_materials(root,[next(
        item for item in seal['materials'] if item['source']==str(c.workspace(root,2)/'artifacts/revised.py'))])[0]
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
    c.write(review_record/'native-outcome.json',dict(returncode=0,stop_reason=None,native_started=False,
        usage=native.usage(root),raw_ref=c.file_ref(review_record/'science-output.json')))
    c.seal_result(root,4)
    verdict=q._read(root,c.read(review_record/'result-identity.json')['formal_review'])
    assert verdict['status']=='passed' and verdict['independence']=='independent' and verdict['subject']['material']==material
    # Exact host ownership and complete runtime inventory remain independent
    # of the separately fixed authored code and frozen producer inputs.
    changed=deepcopy(first_accepted);changed['input_sources']['view']['job']['path']='jobs/001.json'
    with pytest.raises(q.IntegrityError,match='exact host owner'):
        inputs.validate_local_input_sources(root,job,changed)
    changed=deepcopy(first_accepted);changed['input_sources']['view']['files'].append(
        dict(name='unbound.txt',origin=dict(kind='unknown')))
    with pytest.raises(q.IntegrityError,match='scientific inventory'):
        inputs.validate_local_input_sources(root,job,changed)
    changed=deepcopy(first_accepted);changed['code_version']['entry']='other.py'
    with pytest.raises(q.IntegrityError,match='exact selected source or original pin'):
        inputs.validate_local_input_sources(root,job,changed)
    # These programs did not declare this H input as a runtime contribution.
    # Consuming its damaged bound original still fails with an integrity error;
    # its failure cannot be relabeled as a repairable author-file selection.
    saved=(record/'provenance.json').read_bytes()
    target=originals.material_path(root,producer_material);target.chmod(0o600);target.write_text('changed retained input\n')
    with pytest.raises(e.IntegrityError,match='Scientific input'):
        experiment_client.input_reference(config,work/producer_material['path'])
    assert (record/'provenance.json').read_bytes()==saved


@pytest.mark.process_e2e
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
