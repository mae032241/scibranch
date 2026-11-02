"""A verified author file error returns to the original W within existing caps."""
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import (contracts as c, continuation, execution,
    experiment_client, experiments, native_recovery, worker_delivery)
from scidiscovery.research_loop.output_repair import WorkRepairRequired
from execution_fixtures import execution_registry


TRACEBACK = '''Traceback (most recent call last):
  File "/private/author/finish.py", line 9, in <module>
    destination.write_text("finish")
PermissionError: [Errno 13] Permission denied: '/private/host/secret-file'
'''


@pytest.mark.parametrize('status,text,expected',[
    ('known_failed',TRACEBACK,True),
    ('known_success',TRACEBACK,False),
    ('unknown',TRACEBACK,False),
    ('running',TRACEBACK,False),
    ('known_failed','PermissionError: [Errno 13] Permission denied: fixture',False),
    ('known_failed',TRACEBACK.replace('PermissionError','RuntimeError'),False),
    ('known_failed',TRACEBACK.replace('[Errno 13]','[Errno 5]'),False),
    ('known_failed',TRACEBACK+'A scientific explanation follows.\n',False),
    ('known_failed','Traceback (most recent call last):\n',False),
])
def test_only_confirmed_python_permission_error_has_typed_file_diagnosis(status,text,expected):
    failure=experiments.author_file_failure(status,text)
    assert (failure is not None) is expected
    if failure is not None:
        assert failure['error']=='PermissionError' and failure['errno']=='EACCES'
        assert '/private/' not in json.dumps(failure)


def file_inventory(tmp_path,*,call_id='0003-original'):
    record=tmp_path/'record';record.mkdir()
    job=dict(role='W',step=5)
    c.write(record/'science-output.json',dict(stage_intent='review'))
    accepted=tmp_path/'accepted.json'
    c.write(accepted,dict(native_attempt=dict(number=0)))
    log=tmp_path/'stderr.txt';log.write_text('warning\n'*(64*1024)+TRACEBACK)
    output=dict(name='execution-logs-2/stderr.txt',path='stderr.txt',size_bytes=log.stat().st_size)
    call=dict(call_id=call_id,status='known_failed',mechanism_binding=None,
        accepted=dict(path='accepted.json',snapshot='accepted.json',retention='archived'),
        completed=dict(path='completed.json',snapshot='completed.json',retention='archived'),
        terminal=None,observation=dict(path='observation.json',snapshot='observation.json',retention='archived'))
    failure=experiments.read_author_file_failure(call['status'],[dict(output,path=str(log))])
    call['execution']=dict(public_result=dict(author_file_error=dict(failure,source_attempt=0)))
    inventory=c.CurrentInventory(tmp_path,job,record,0,[call],{call['call_id']:[output]},
        task_ref=dict(path='jobs/005.json'))
    inventory.file_correction=worker_delivery._file_correction(tmp_path,job,inventory=inventory)
    inventory.delivery=worker_delivery._capture(tmp_path,job,record,inventory=inventory)
    return inventory,log


def test_owner_reads_error_once_and_consumer_uses_frozen_call_bound_values(tmp_path):
    inventory,log=file_inventory(tmp_path)
    failure=inventory.file_correction
    assert failure['call_id']=='0003-original'
    assert failure['accepted']==inventory.managed_calls[0]['accepted']
    assert failure['completed']==inventory.managed_calls[0]['completed']
    assert failure['observation']==inventory.managed_calls[0]['observation']
    assert failure['log']=='work/retained-experiments/0003-original/outputs/execution-logs-2/stderr.txt'
    log.unlink()  # Delivery consumers must never reopen the producer's stderr.
    science=dict(stage_intent='review')
    assessment=worker_delivery.assessment(tmp_path,inventory.job,science,inventory.record,inventory=inventory)
    assert assessment['status']=='incomplete' and assessment['file_correction']==failure
    with pytest.raises(WorkRepairRequired) as raised:
        worker_delivery.require_delivery(tmp_path,inventory.job,science,inventory.record,inventory=inventory)
    assert raised.value.inventory is inventory
    diagnostic=raised.value.request()['diagnostic']
    for required in ('PermissionError','EACCES',failure['log'],'Reuse retained results','Do not retrain'):
        assert required in diagnostic
    assert '/private/' not in diagnostic


@pytest.mark.parametrize('call_id',['0003-original','a'*128],ids=['normal-call','max-call-id'])
def test_file_repair_wire_budget_keeps_log_safety_and_author_choice(tmp_path,call_id):
    inventory,_=file_inventory(tmp_path,call_id=call_id)
    with pytest.raises(WorkRepairRequired) as raised:
        worker_delivery.require_delivery(tmp_path,inventory.job,dict(stage_intent='review'),
            inventory.record,inventory=inventory)
    diagnostic=raised.value.request()['diagnostic']
    wire=lambda value:json.dumps(json.dumps(value,ensure_ascii=False),ensure_ascii=False).encode('utf-8')
    assert len(wire(diagnostic))-len(wire(''))<=512
    for required in ('PermissionError','EACCES',inventory.file_correction['log'],
            'Reuse retained results','Do not retrain','rerun completed experiments',
            'copy retained originals again','reject all candidates','failed call',
            'permissions and budget','stage_intent=decision','state any blocker'):
        assert required in diagnostic


@pytest.mark.parametrize('intent,findings,reason',[
    ('decision',None,'worker_decision'),
    ('review',[dict(mechanism='accepted 0003-original',basis='blocked',finding='Required closeout permission is unavailable.')],
        'scientific_blocked'),
])
def test_explicit_author_decision_or_blocker_ends_file_repair(tmp_path,intent,findings,reason):
    inventory,_=file_inventory(tmp_path)
    science=dict(stage_intent=intent,mechanism_findings=findings)
    result=worker_delivery.require_delivery(tmp_path,inventory.job,science,inventory.record,inventory=inventory)
    assert result['status']=='partial' and result['reason']==reason


@pytest.mark.parametrize('status',['known_success','unknown','not_submitted'])
def test_last_current_call_supersedes_file_error_without_reopening_prior_logs(tmp_path,status):
    inventory,log=file_inventory(tmp_path)
    log.unlink()
    inventory.managed_calls.append(dict(call_id='0004-latest',status=status))
    assert worker_delivery._file_correction(tmp_path,inventory.job,inventory=inventory) is None


def test_old_attempt_error_is_not_a_new_delivery_failure_even_with_no_candidates(tmp_path):
    inventory,log=file_inventory(tmp_path)
    log.unlink()
    inventory.attempt=1
    assert worker_delivery._file_correction(tmp_path,inventory.job,inventory=inventory) is None
    inventory.file_correction=None
    science=dict(stage_intent='review',candidate_nominations=dict(representatives=[],extras=[],gaps=[]))
    assert worker_delivery.assessment(tmp_path,inventory.job,science,inventory.record,inventory=inventory)['status']=='not_applicable'


@pytest.mark.parametrize('field', ['interface','status','log','attempt'])
def test_frozen_file_error_requires_its_local_failed_original_and_log(field):
    failure=dict(experiments.author_file_failure('known_failed',TRACEBACK),log_name='execution-logs/stderr.txt',source_attempt=0)
    observation=dict(interface=dict(executor='local_execution:command'),status='known_failed',
        public_result=dict(author_file_error=failure),scientific_outputs=[dict(name=failure['log_name'])])
    accepted=dict(native_attempt=dict(number=0))
    experiments._validate_author_file_failure(observation,accepted)
    if field=='interface':observation['interface']['executor']='another:executor'
    elif field=='status':observation['status']='known_success'
    elif field=='log':observation['scientific_outputs']=[]
    else:failure['source_attempt']=1
    with pytest.raises(experiments.IntegrityError,match='verified Local failure'):
        experiments._validate_author_file_failure(observation,accepted)


FILE_FAILURE_NATIVE = r'''
 if config['fault']=='caller-no-search':
  import subprocess
  from scidiscovery.research_loop.worker_execution_bridge import SUBMISSION_DIRECTORY
  bridge=[sys.executable,'-m','scidiscovery.research_loop.worker_execution_bridge',
   '--submission-directory',str(work/SUBMISSION_DIRECTORY)]
  code=work/'code';code.mkdir(exist_ok=True)
  failure_file=work/'work/file-failure.json'
  if not resume:
   script=code/'file-failure.py'
   script.write_text('from pathlib import Path\n'
    'Path("artifacts/model.bin").write_bytes(b"retained trained model")\n'
    'Path("artifacts/audit.json").write_text(\'{"audit":"retained original"}\')\n'
    'path=Path("artifacts/readonly.py");path.write_text("original source");path.chmod(0o400)\n'
    'path.write_text("duplicate packaging")\n')
   outcome=subprocess.run([*bridge,'run','--script',str(script),
    '--experiment-dir',str(work/'experiments/original')],capture_output=True,text=True)
   response=json.loads(outcome.stdout)
   assert outcome.returncode==1 and response['status']=='known_failed',(outcome.stdout,outcome.stderr)
   assert response['handoff']['reason']=='author_file_error',response
   assert response['handoff']['author_file_error']['error']=='PermissionError'
   log=response['handoff']['author_file_error']['log']
   assert (work/log).is_file() and 'PermissionError' in (work/log).read_text()
   failure_file.write_text(json.dumps(response))
  else:
   assert 'PermissionError' in prompt and 'Reuse retained results' in prompt
   assert 'Do not retrain' in prompt and 'copy retained originals again' in prompt
   (work/'work/file-repair-prompt.txt').write_text(prompt)
   failure=json.loads(failure_file.read_text())
   original=work/'work/retained-experiments'/failure['call_id']/'outputs'
   assert (original/'model.bin').read_bytes()==b'retained trained model'
   assert json.loads((original/'audit.json').read_text())==dict(audit='retained original')
   mode=os.environ['SCID_FILE_REPAIR_MODE']
   if mode=='closeout':
    script=code/'closeout.py'
    declared=dict(version=1,metrics=[],outputs=[dict(path='artifacts/closeout.txt')],candidates=[])
    script.write_text('from pathlib import Path\n'
     'Path("artifacts/closeout.txt").write_text("Original model/audit remain retained; author file closeout completed without retraining or copying them.")\n'
     'Path("artifacts/result.json").write_text('+repr(json.dumps(declared))+')\n')
    outcome=subprocess.run([*bridge,'run','--script',str(script),
     '--experiment-dir',str(work/'experiments/closeout')],capture_output=True,text=True)
    response=json.loads(outcome.stdout)
    assert outcome.returncode==0 and response['status']=='known_success',(outcome.stdout,outcome.stderr)
    (work/'work/file-closeout.json').write_text(json.dumps(response))
   elif mode=='decision':
    payload['stage_intent']='decision'
    payload['limitations'].append('The current interface cannot register the old model without copying; preserve that original and ask A to decide.')
   elif mode=='reject-all':
    payload['scientific_result']='I judge all retained models unsuitable; no candidate is nominated and no further file correction is needed.'
    payload['candidate_nominations']=dict(representatives=[],extras=[],gaps=[])
   else:
    assert mode=='repeat'
    script=code/'still-blocked.py'
    script.write_text('from pathlib import Path\n'
     'path=Path("artifacts/readonly.py");path.write_text("new original source");path.chmod(0o400)\n'
     'path.write_text("new author file mistake")\n')
    number=(work/'work/launches').read_text().count('resume')
    outcome=subprocess.run([*bridge,'run','--script',str(script),
     '--experiment-dir',str(work/('experiments/still-blocked-'+str(number)))],capture_output=True,text=True)
    response=json.loads(outcome.stdout)
    assert outcome.returncode==1 and response['handoff']['reason']=='author_file_error',(outcome.stdout,outcome.stderr)
  config['fault']='file-failure-handled'
'''


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode',['closeout','decision','reject-all','repeat'])
def test_cli_resumes_original_w_with_finite_file_repair_and_then_seals(tmp_path,monkeypatch,execution_registry,mode):
    import test_default_cli_output_repair as fixture
    native=fixture.NATIVE.replace("elif role=='W':\n payload.update(stage_intent='review',mechanism_findings=None)\n",
        "elif role=='W':\n payload.update(stage_intent='review',mechanism_findings=None)\n"+FILE_FAILURE_NATIVE)
    assert native!=fixture.NATIVE
    monkeypatch.setattr(fixture,'NATIVE',native)
    root,home,env=fixture.prepare_cli(tmp_path,fault='caller-no-search')
    env=dict(env,SCID_FILE_REPAIR_MODE=mode)
    frozen={path:path.read_bytes() for path in (root/'settings.json',home/'config.toml',home/'auth.json')}
    result=fixture.cli(root,'run',env=env,trace_timeout=True)
    assert result['status']=='finish',fixture.nomination_diagnostic(root,result,fault='file-failure-'+mode)
    work=c.workspace(root,2);chain=execution.records(root,2)
    assert len(chain)==(3 if mode=='repeat' else 2)
    assert not worker_delivery.applicable(c.read(root/'jobs/002.json'))
    initial=c.read(work/'work/file-failure.json');call=initial['call_id']
    directory=experiment_client.record_directory(root,2)/'calls'/call
    assert c.read(directory/'completed.json')['status']=='known_failed'
    assert c.read(directory/'completed.json')['returncode']==1
    model=work/'work/retained-experiments'/call/'outputs/model.bin'
    audit=work/'work/retained-experiments'/call/'outputs/audit.json'
    assert model.read_bytes()==b'retained trained model'
    assert c.read(audit)==dict(audit='retained original')
    assert not (chain[0]/'stage-end.json').exists()
    for number,record in enumerate(chain[1:],1):
        intent=c.read(record/'native-intent.json')
        assert intent['continuation_kind']=='automatic_recovery'
        assert intent['automatic_recovery']['action']=='continue'
        assert intent['automatic_recovery']['source_attempt']==number-1
        assert intent['session']['thread_id']==c.read(chain[0]/'native-outcome.json')['thread_id']
        assert intent.get('native_profile')==c.read(chain[0]/'native-intent.json').get('native_profile')
        assert intent['work_repair']['code']=='worker_delivery_incomplete'
        diagnostic=intent['work_repair']['diagnostic']
        source=c.read(record.parent.parent/'work-evidence.json') if number==1 else c.read(chain[number-1]/'work-evidence.json')
        for required in ('PermissionError','EACCES',source['file_correction']['log'],
                'Reuse retained results','Do not retrain','copy retained originals again','reject all candidates',
                'permissions and budget','stage_intent=decision','state any blocker'):
            assert required in diagnostic
        assert str(root) not in diagnostic and str(home) not in diagnostic
        assert intent['budget_slice']['previous_tokens']==14*number
    calls=experiment_client.record_directory(root,2)/'calls'
    assert len(list(calls.iterdir()))==(2 if mode=='closeout' else 3 if mode=='repeat' else 1)
    if mode=='closeout':
        closeout=c.read(work/'work/file-closeout.json')
        assert closeout['call_id']!=call
        assert c.read(calls/closeout['call_id']/'accepted.json')['native_attempt']['number']==1
        assert closeout['result']['candidates']==[]
        completed=c.read(calls/closeout['call_id']/'completed.json')
        assert {item['name'] for item in completed['outputs']}=={'closeout.txt','result.json'}
    elif mode=='decision':
        assert c.read(chain[-1]/'science-output.json')['stage_intent']=='decision'
    elif mode=='repeat':
        ending=c.read(chain[-1]/'stage-end.json')
        assert ending['reason']=='automatic_recovery_continue_limit'
        assert ending['work_exit']['counters']['continues']==2
    else:
        science=c.read(chain[-1]/'science-output.json')
        assert science['stage_intent']=='review' and science['candidate_nominations']==dict(representatives=[],extras=[],gaps=[])
    assert (work/'work/launches').read_text()=='exec\n'+'resume\n'*(len(chain)-1)
    assert c.read(root/'jobs/003.json')['role']=='A'
    assert not (root/'jobs/004.json').exists()
    originals={path:path.read_bytes() for path in (directory/'accepted.json',directory/'completed.json',
        directory/'terminal.json',directory/'execution-observation.json',model,audit,chain[0]/'science-output.json')}
    assert native_recovery.counters(c.read(path/'native-intent.json') for path in chain)['continues']==len(chain)-1
    assert fixture.cli(root,'run',env=env)['status']=='finish'
    assert execution.records(root,2)==chain
    assert all(path.read_bytes()==data for path,data in {**frozen,**originals}.items())
