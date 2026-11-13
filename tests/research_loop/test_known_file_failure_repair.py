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
    c.write(record/'science-output.json',dict(stage_intent='continue'))
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
    science=dict(stage_intent='continue')
    assessment=worker_delivery.assessment(tmp_path,inventory.job,science,inventory.record,inventory=inventory)
    assert assessment['status']=='incomplete' and assessment['file_correction']==failure
    with pytest.raises(WorkRepairRequired) as raised:
        worker_delivery.require_delivery(tmp_path,inventory.job,science,inventory.record,inventory=inventory)
    assert raised.value.inventory is inventory
    diagnostic=raised.value.request()['diagnostic']
    for required in ('PermissionError','EACCES',failure['log'],'Reuse retained results','Do not retrain'):
        assert required in diagnostic
    assert '/private/' not in diagnostic


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
