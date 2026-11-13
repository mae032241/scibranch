"""Completed transport can leave a precisely identified scientific delivery gap."""
import pytest

from scidiscovery.research_loop import (contracts as c, continuation, execution,
    experiment_client, experiments, native, native_recovery)


def test_completed_delivery_and_incomplete_native_share_original_continue_allowance():
    limits=native_recovery.policy(dict(max_continues=1))
    used=native_recovery.counters([dict(automatic_recovery=dict(action='continue',wait_seconds=0.))])
    assert native_recovery.work_decision(limits,used,session_available=True)['reason']=='automatic_recovery_continue_limit'
    used=native_recovery.counters([])
    assert native_recovery.work_decision(limits,used,session_available=False)['reason']=='automatic_recovery_session_unavailable'
    assert native_recovery.work_decision(limits,used,session_available=True)['action']=='continue'


def test_native_wait_expired_unknown_without_same_outcome_remains_unknown(tmp_path,monkeypatch):
    record=tmp_path/'record';record.mkdir()
    other=tmp_path/'other-record';other.mkdir()
    c.write(other/'native-outcome.json',dict(returncode=0,terminal_observed=True))
    monkeypatch.setattr(execution,'record',lambda *_:record)
    clock=[0.];monkeypatch.setattr(native.time,'monotonic',lambda:clock[0])
    def state(*_):
        clock[0]=6.
        return 'unknown'
    monkeypatch.setattr(native.runtime,'node_state',state)
    starts=[];seals=[]
    monkeypatch.setattr(native.runtime,'start',lambda *args:starts.append(args))
    monkeypatch.setattr(continuation,'seal_or_repair',lambda *args:seals.append(args))
    with pytest.raises(RuntimeError,match='unknown; preserved without replay'):
        native.wait_native(tmp_path,1)
    assert starts==[] and seals==[] and not (record/'native-outcome.json').exists()


def test_original_call_configuration_uses_exact_accepted_record_reference(tmp_path,monkeypatch):
    from types import SimpleNamespace
    root=tmp_path/'instance';record=root/'native-evidence/002'
    record.mkdir(parents=True)
    (root/'jobs').mkdir()
    c.write(root/'jobs/002.json',dict(role='W',step=2))
    c.write(root/'jobs/003.json',dict(role='W',step=3))
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/002.json')))
    (record/'continuations/001').mkdir(parents=True)
    c.write(record/'continuations/001/native-intent.json',dict(attempt=1,task_ref=c.record_ref(root/'jobs/002.json')))
    original_attempt=dict(number=0,execution_key=c.record_ref(root/'jobs/002.json'))
    current=dict(scope=dict(step=2),record_dir=str(root/'records'),execution_interface=dict(version=1),
        native_attempt=dict(number=1,execution_key=c.record_ref(record/'continuations/001/native-intent.json')))
    accepted=dict(native_attempt=original_attempt,interface=current['execution_interface'])
    original=dict(current,native_attempt=original_attempt)
    projection=root/'host-runtime/experiment-control/002/interfaces/000.json'
    projection.parent.mkdir(parents=True)
    c.write(projection,original)
    monkeypatch.setattr(experiments,'Records',lambda *_:SimpleNamespace(accepted=lambda _:accepted))
    monkeypatch.setattr(experiment_client,'root_for_configuration',lambda *_:root)
    monkeypatch.setattr(execution,'accepted_intents',lambda *_:iter([(record,{})]))
    assert experiments.original_configuration(current,'first-original')==original
    accepted['native_attempt']=dict(number=0,execution_key=c.record_ref(root/'jobs/003.json'))
    with pytest.raises(experiments.IntegrityError,match='exact accepted intent'):
        experiments.original_configuration(current,'first-original')
    accepted['native_attempt']=dict(number=0,execution_key='1'*64)
    with pytest.raises(experiments.IntegrityError,match='exact accepted intent'):
        experiments.original_configuration(current,'first-original')
