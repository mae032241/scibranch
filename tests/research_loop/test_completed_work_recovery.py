"""Completed transport can leave a precisely identified scientific delivery gap."""
import pytest

from scidiscovery.research_loop import (budget, contracts as c, continuation, execution,
    experiment_client, experiments, native, native_recovery, worker_delivery, checkpoint)
from execution_fixtures import execution_registry
from test_default_cli_output_repair import cli, nomination_diagnostic, prepare_cli


def test_completed_delivery_and_incomplete_native_share_original_continue_allowance():
    limits=native_recovery.policy(dict(max_continues=1))
    used=native_recovery.counters([dict(automatic_recovery=dict(action='continue',wait_seconds=0.))])
    assert native_recovery.work_decision(limits,used,session_available=True)['reason']=='automatic_recovery_continue_limit'
    used=native_recovery.counters([])
    assert native_recovery.work_decision(limits,used,session_available=False)['reason']=='automatic_recovery_session_unavailable'
    assert native_recovery.work_decision(limits,used,session_available=True)['action']=='continue'


@pytest.mark.parametrize('invalid_terminal',[False,True])
def test_native_wait_collects_same_outcome_published_during_expired_unknown_probe(tmp_path,monkeypatch,invalid_terminal):
    root=tmp_path;record=root/'record';record.mkdir();(root/'jobs').mkdir()
    c.write(root/'jobs/001.json',dict(role='H',step=1))
    monkeypatch.setattr(execution,'record',lambda *_:record)
    monkeypatch.setattr(execution,'origin',lambda *_:record)
    monkeypatch.setattr(checkpoint,'enabled',lambda *_:False)
    clock=[0.];monkeypatch.setattr(native.time,'monotonic',lambda:clock[0])
    probes=[];seals=[];starts=[]
    def state(*_):
        probes.append('same original')
        if len(probes)==1:
            c.write(record/'native-outcome.json',dict(returncode='invalid' if invalid_terminal else 0,terminal_observed=True))
            clock[0]=6.
            return 'unknown'  # The completed probe predates this publication.
        return 'terminal'
    def seal(*_):
        seals.append(record)
        outcome=c.read(record/'native-outcome.json')
        if type(outcome['returncode']) is not int:raise ValueError('Original terminal failed its normal seal checks')
        assert outcome['terminal_observed'] is True
        c.write(record/'result-identity.json',dict(original='same accepted outcome'))
        return dict(summary='Same original was collected')
    monkeypatch.setattr(native.runtime,'node_state',state)
    monkeypatch.setattr(native.runtime,'start',lambda *args:starts.append(args))
    monkeypatch.setattr(continuation,'automatic_decision',lambda *_:dict(action='none'))
    monkeypatch.setattr(continuation,'seal_or_repair',seal)
    if invalid_terminal:
        with pytest.raises(ValueError,match='normal seal checks'):native.wait_native(root,1)
        assert not (record/'result-identity.json').exists()
    else:
        assert native.wait_native(root,1)['summary']=='Same original was collected'
        assert c.read(record/'result-identity.json')['original']=='same accepted outcome'
    assert len(probes)==2 and seals==[record] and starts==[]
    assert not (record/'continuations').exists()


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


def test_enforced_original_fragment_review_precedes_completed_work_recovery(tmp_path,monkeypatch):
    from test_automatic_recovery import arbiter_instance
    root,record=arbiter_instance(tmp_path,monkeypatch)
    outcome=c.read(record/'native-outcome.json');outcome['terminal_observed']=True
    c.write(record/'native-outcome.json',outcome)
    monkeypatch.setattr(continuation,'_completed_work_request',lambda *_:dict(code='worker_delivery_incomplete'))
    monkeypatch.setattr(checkpoint,'enabled',lambda *_:True)
    monkeypatch.setattr(continuation,'fragment_review',lambda *_:dict(requested=True,enforced=True,reason='host_investment_review'))
    before=(record/'native-intent.json').read_bytes()
    decision=continuation.automatic_decision(root,1)
    assert decision['action']=='none' and decision['reason']=='worker_fragment_review_requested'
    assert decision['counters']==dict(continues=0,retries=0,wait_seconds=0.)
    assert execution.records(root,1)==[record] and (record/'native-intent.json').read_bytes()==before
    assert not (record/'continuations').exists()


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


@pytest.mark.parametrize('job,prior',[
    (dict(role='A'),[{}]),
    (dict(role='W',final_only=True),[{}]),
    (dict(role='W',purpose='closeout'),[{}]),
    (dict(role='W',formal_review=dict(subject='original')),[{}]),
    (dict(role='W'),[{},dict(continuation_kind='execution_wake')]),
    (dict(role='W'),[{},dict(continuation_kind='startup_successor')]),
    (dict(role='W'),[{},dict(continuation_kind='author_closeout')]),
])
def test_completed_delivery_cannot_promote_special_original_scope(tmp_path,job,prior):
    (tmp_path/'jobs').mkdir();c.write(tmp_path/'jobs/001.json',job)
    record=tmp_path/'record';record.mkdir()
    c.write(record/'work-evidence.json',dict(unusable='This marker grants no delivery or execution facts.'))
    assert continuation._completed_work_request(tmp_path,1,record,0,prior) is None


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault', ['completed-findings', 'completed-missing-output'])
def test_default_completed_work_continues_real_gap_to_explicit_decision_without_replaying_call(tmp_path,execution_registry,fault):
    root,home,env=prepare_cli(tmp_path,fault=fault)
    originals={path:path.read_bytes() for path in (root/'settings.json',home/'config.toml',home/'auth.json')}
    result=cli(root,'run',env=env)
    assert result['status']=='finish',nomination_diagnostic(root,result,fault=fault)
    job=c.read(root/'jobs/002.json');chain=execution.records(root,2)
    assert [row['mechanism_ref'] for row in job['search']['coverage_obligation']]==['h001.m1','h001.m2']
    assert len(chain)==(3 if fault=='completed-missing-output' else 2)
    initial,current=chain[0],chain[-1]
    intent=c.read(current/'native-intent.json')
    assert intent['continuation_kind']=='automatic_recovery'
    assert intent['automatic_recovery']['action']=='continue'
    assert intent['automatic_recovery']['reason']=='worker_delivery_incomplete'
    assert intent['automatic_recovery']['source_attempt']==len(chain)-2
    assert intent['work_repair']==continuation.output_request(root,2,chain[-2],source_attempt=len(chain)-2)
    assert intent['work_repair']['work_evidence_ref']==c.record_ref(chain[-2]/'work-evidence.json')
    assert intent['task_ref']==c.read(initial/'native-intent.json')['task_ref']
    assert intent['root_intent_ref']==c.record_ref(initial/'native-intent.json')
    assert intent['session']['home']==str(home)
    assert intent['session']['thread_id']==c.read(initial/'native-outcome.json')['thread_id']
    assert intent['budget_slice']['previous_tokens']==14*(len(chain)-1)
    assert native_recovery.counters(c.read(path/'native-intent.json') for path in chain)['continues']==1
    if fault=='completed-missing-output':
        repair=c.read(chain[1]/'native-intent.json')
        assert repair['continuation_kind']=='repair' and repair['output_repair']['code']=='missing_output'
        assert repair.get('work_repair') is None and repair.get('automatic_recovery') is None
    else:
        assert c.read(initial/'science-output.json')['mechanism_findings'] is None
        assert c.read(initial/'native-outcome.json')['terminal_observed'] is True
    science=c.validate(c.workspace(root,2))
    assessment=worker_delivery.assessment(root,job,science,current)
    assert assessment['status']=='partial' and assessment['reason']=='worker_decision'
    assert assessment['missing']==['h001.m2'] and science['stage_intent']=='decision'
    previous=chain[-2]
    previous_science=c.read(previous/'science-output.json')
    previous_gap=worker_delivery.assessment(root,job,previous_science,previous,source_attempt=len(chain)-2)
    assert previous_gap['status']=='incomplete' and previous_gap['missing']==['h001.m2']
    assert len(c.read(previous/'work-evidence.json')['evidence'])==1
    seal=c.read(initial/'result-identity.json')
    assert seal['control_outcome']['worker_delivery']['status']=='partial'
    assert seal['control_outcome']['worker_delivery']['reason']=='worker_decision'
    assert c.settings(root)['resume_policy']['output_only'] is True
    assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'+'resume\n'*(len(chain)-1)
    calls=experiment_client.record_directory(root,2)/'calls'
    locator=c.read(c.workspace(root,2)/'work/retained-candidate-locator.json')
    original_call=calls/locator['call_id']
    assert list(calls.iterdir())==[original_call]
    retained={path:path.read_bytes() for path in (original_call/'accepted.json',original_call/'completed.json',initial/'science-output.json') if path.exists()}
    meter=native.usage(root)
    assert budget.used_steps(root)==len(chain)+2
    assert cli(root,'run',env=env)['status']=='finish'
    assert list(calls.iterdir())==[original_call] and len(execution.records(root,2))==len(chain)
    assert native.usage(root)==meter
    assert all(path.read_bytes()==raw for path,raw in {**originals,**retained}.items())
    continuation.validate_automatic(root,2,intent)
    forged=dict(intent,work_repair=dict(intent['work_repair'],source_attempt=99))
    with pytest.raises(ValueError,match='exact original delivery failure|exact completed source'):
        continuation.validate_automatic(root,2,forged)
    with pytest.raises(ValueError,match='source attempt changed'):
        worker_delivery.validate(root,job,current,source_attempt=0)


@pytest.mark.process_e2e
def test_default_completed_work_can_execute_missing_assigned_experiment(tmp_path,execution_registry):
    root,_,env=prepare_cli(tmp_path,fault='completed-experiment')
    result=cli(root,'run',env=env)
    assert result['status']=='finish',nomination_diagnostic(root,result,fault='completed-experiment')
    origin,current=execution.records(root,2)
    intent=c.read(current/'native-intent.json')
    assert intent['continuation_kind']=='automatic_recovery' and intent['work_repair']['code']=='worker_delivery_incomplete'
    calls=experiment_client.record_directory(root,2)/'calls'
    first=c.read(c.workspace(root,2)/'work/retained-candidate-locator.json')['call_id']
    second=c.read(c.workspace(root,2)/'work/second-original-call.json')['call_id']
    assert first!=second and {path.name for path in calls.iterdir()}=={first,second}
    assert c.read(calls/first/'accepted.json')['native_attempt']['number']==0
    assert c.read(calls/second/'accepted.json')['native_attempt']['number']==1
    assert len(c.read(origin/'work-evidence.json')['evidence'])==1
    assert len(c.read(current/'work-evidence.json')['evidence'])==2
    job=c.read(root/'jobs/002.json');science=c.validate(c.workspace(root,2))
    assert worker_delivery.assessment(root,job,science,current)['status']=='complete'
    assert len(science['mechanism_findings'])==2
    retained={path:path.read_bytes() for call in (first,second) for path in
        (calls/call/'accepted.json',calls/call/'completed.json',calls/call/'execution-observation.json')}
    assert cli(root,'run',env=env)['status']=='finish'
    assert len(execution.records(root,2))==2 and {path.name for path in calls.iterdir()}=={first,second}
    assert all(path.read_bytes()==raw for path,raw in retained.items())


@pytest.mark.process_e2e
def test_completed_work_continue_cap_retains_partial_and_original_call(tmp_path,execution_registry):
    root,_,env=prepare_cli(tmp_path,fault='completed-repeat')
    result=cli(root,'run',env=env,trace_timeout=True)
    assert result['status']=='finish',nomination_diagnostic(root,result,fault='completed-repeat')
    chain=execution.records(root,2);assert len(chain)==3
    job=c.read(root/'jobs/002.json');current=chain[-1]
    used=native_recovery.counters(c.read(path/'native-intent.json') for path in chain)
    assert used==dict(continues=2,retries=0,wait_seconds=0.)
    exit=c.read(current/'stage-end.json')['work_exit']
    assert exit['kind']=='automatic_continue_cap' and exit['counters']==used
    assert worker_delivery.validate_exit(root,job,current,exit)=='automatic_recovery_continue_limit'
    retained_delivery=worker_delivery.require_delivery(root,job,c.validate(c.workspace(root,2)),current)
    assert retained_delivery['status']=='partial' and retained_delivery['missing']==['h001.m2']
    assert len(c.read(current/'work-evidence.json')['evidence'])==1
    assert len(list((experiment_client.record_directory(root,2)/'calls').iterdir()))==1
    assert (c.workspace(root,2)/'work/launches').read_text()=='exec\nresume\nresume\n'
    assert c.read(root/'jobs/003.json')['role']=='A'
    assert not (root/'jobs/004.json').exists()
    forged=dict(exit,counters=dict(used,continues=1))
    with pytest.raises(ValueError,match='exhausted original automatic continue cap'):
        worker_delivery.validate_exit(root,job,current,forged)


@pytest.mark.process_e2e
def test_completed_work_budget_denial_keeps_original_call_and_verified_partial_without_new_attempt(tmp_path,execution_registry):
    root,_,env=prepare_cli(tmp_path,fault='completed-findings',max_tokens=28)
    result=cli(root,'run',env=env)
    origin,=execution.records(root,2)
    assert (origin/'result-identity.json').exists(),nomination_diagnostic(root,result,fault='completed-findings')
    job=c.read(root/'jobs/002.json')
    exit=c.read(origin/'stage-end.json')['work_exit']
    assert exit['kind']=='budget' and exit['reason'] in {'worker_resume_budget_unavailable','token_limit'}
    assert exit['consumption']['tokens']==14
    assert exit['shared']['max_tokens']==28 and exit['meter']['observed_tokens']>=28
    assert worker_delivery.validate_exit(root,job,origin,exit)==exit['reason']
    retained_delivery=worker_delivery.require_delivery(root,job,c.validate(c.workspace(root,2)),origin)
    assert retained_delivery['status']=='partial' and retained_delivery['missing']==['h001.m2']
    assert budget.used_steps(root)>=2 and native.usage(root)['observed_tokens']>=28
    calls=experiment_client.record_directory(root,2)/'calls'
    assert len(list(calls.iterdir()))==1
    assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'
    retained={path:path.read_bytes() for call in calls.iterdir() for path in (call/'accepted.json',call/'completed.json')}
    cli(root,'run',env=env)
    assert len(execution.records(root,2))==1 and len(list(calls.iterdir()))==1
    assert all(path.read_bytes()==raw for path,raw in retained.items())
    forged=dict(exit,reason='wall_limit')
    with pytest.raises(ValueError,match='exact exit'):
        worker_delivery.validate_exit(root,job,origin,forged)


@pytest.mark.process_e2e
@pytest.mark.parametrize('automatic',[{'enabled':False},None])
def test_completed_work_without_independent_automatic_grant_preserves_output_only_partial(tmp_path,execution_registry,automatic):
    # fragment=True selects an explicit old output-only policy with no automatic
    # grant; the disabled variant adds an explicit independent false grant.
    root,_,env=prepare_cli(tmp_path,fault='completed-findings',fragment=automatic is None,automatic=automatic)
    result=cli(root,'run',env=env)
    assert result['status']=='finish',nomination_diagnostic(root,result,fault='completed-findings')
    chain=execution.records(root,2);assert len(chain)==1
    exit=c.read(chain[0]/'stage-end.json')['work_exit']
    assert exit['kind']=='resume_policy' and exit['reason']=='worker_resume_output_only'
    job=c.read(root/'jobs/002.json')
    assert worker_delivery.assessment(root,job,c.validate(c.workspace(root,2)),chain[0])['missing']==['h001.m2']
    assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'
    assert len(list((experiment_client.record_directory(root,2)/'calls').iterdir()))==1


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault,reason',[('completed-blocked','scientific_blocked'),
    ('completed-decision','worker_decision'),('completed-unresolved','worker_decision')])
def test_completed_valid_partial_or_decision_never_authorizes_more_work(tmp_path,execution_registry,fault,reason):
    root,_,env=prepare_cli(tmp_path,fault=fault)
    result=cli(root,'run',env=env)
    assert result['status']=='finish',nomination_diagnostic(root,result,fault=fault)
    chain=execution.records(root,2);assert len(chain)==1
    result=c.read(chain[0]/'result-identity.json')['control_outcome']['worker_delivery']
    assert result['status']=='partial' and result['reason']==reason
    assert len(c.read(chain[0]/'work-evidence.json')['evidence'])==1
    if fault=='completed-blocked':assert result['blocked']==['h001.m2']
    else:
        science=c.validate(c.workspace(root,2))
        assert science['stage_intent']=='decision' and result['missing']==['h001.m2']
        if fault=='completed-unresolved':assert isinstance(science['mechanism_findings'],str)
    assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'
    assert len(list((experiment_client.record_directory(root,2)/'calls').iterdir()))==1
