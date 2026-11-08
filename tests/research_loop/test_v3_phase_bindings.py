"""Live declarations apply to ordinary W, with exact legacy maintenance scopes."""
from pathlib import Path

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import contracts as c, history, worker_research
from scidiscovery.research_loop.breadth import OpportunityRequired


@pytest.mark.parametrize('actions',[('revise','revise'),('retire','resume')])
def test_duplicate_batch_target_is_rejected_before_evolution_or_publication(tmp_path,monkeypatch,actions):
    config=dict(research_strategy_version=3,record_dir=str(tmp_path/'records'),
        worker_research_goal_id='goal',scope=dict(step=1,role='W',job_ref=dict(path='/instance/jobs/001.json')))
    state=worker_research.initial(config)
    value=dict(declarations=[dict(mechanism_ref='w001.initial',action=action,question='Question '+str(index))
        for index,action in enumerate(actions)],mechanism_ref='w001.initial',
        experiment_question=dict(question='What changes?',change_criterion='The result changes the next experiment'))
    def forbidden(*args,**kwargs):raise AssertionError('Invalid requests cannot reach evolution or publication')
    monkeypatch.setattr(worker_research,'evolve',forbidden)
    monkeypatch.setattr(worker_research,'publish',forbidden)
    with pytest.raises(OpportunityRequired,match='distinct mechanism_ref'):
        worker_research.prepare(config,state,value)
    assert not (tmp_path/'records').exists()


def test_unique_merge_dependencies_remain_a_valid_batch_request():
    value=dict(declarations=[
        dict(mechanism_ref='w001.merged',action='merge',question='Merged representation',predecessors=['w001.parent']),
        dict(mechanism_ref='w001.parent',action='resume')],mechanism_ref='w001.merged',
        experiment_question=dict(question='Does this representation help?',change_criterion='A changed residual changes the next step'))
    assert worker_research.request(value)==value


def test_engineering_scope_uses_legacy_binding_but_ordinary_v3_stays_strict():
    scope=dict(job_ref=dict(path='/instance/jobs/003.json'))
    ordinary=dict(role='W',step=3,research_strategy_version=3,search=None)
    assert worker_research.applicable(ordinary)
    with pytest.raises(ValueError):history._check_call_binding(ordinary,None,scope)
    with pytest.raises(ValueError):history._check_call_binding(dict(ordinary,purpose='delivery_correction'),None,scope)
    repair=dict(ordinary,engineering_repair=dict(candidate_id='retained',reason='Original implementation fault'))
    assert not worker_research.applicable(repair)
    assert history._check_call_binding(repair,None,scope) is None


def test_retired_v3_historical_call_keeps_its_eight_key_binding_in_observation(tmp_path,execution_registry):
    """Trusted accepted history; real retirement/observation, no new acceptance.

    Socket preparation remains unsupported. Historical native preparation and
    acceptance facts are supplied explicitly; no current grant is fabricated.
    """
    import io
    import test_worker_delivery_retirement as fixture
    from scidiscovery.research_loop import accepted_budget,execution,experiment_client,experiments as e
    from scidiscovery.research_loop.protocol import stage_scope
    from test_scientific_guidance import prepare
    root=prepare(tmp_path,None,research_strategy_version=3)
    job=c.build_job(root,1,'W',[]);work=c.workspace(root,1);record=execution.record(root,1)
    intent=dict(task_ref=c.record_ref(root/'jobs/001.json'))
    c.write(record/'native-intent.json',intent);accepted_budget.accept_native(root,1,record,intent)
    config=dict(transport='socket',socket_path=str(tmp_path/'unused.socket'),scope=stage_scope(root,job),
        record_dir=str(experiment_client.record_directory(root,1)),breadth=None,max_seconds=60,
        native_attempt=dict(number=0,execution_key=execution.identity(root,1)),admission=dict(version=1,mode='hard'),
        research_strategy_version=3,worker_research_goal_id=c.settings(root)['goal_id'])
    config['execution_interface']=dict(version=e.VERSION,executor='tcad_execution:tcad',
        code=[e.proof(Path(__file__).resolve())],observation_version=e.OBSERVATION_VERSION,
        terminal_evidence=fixture.TCADManagedExecution.terminal_evidence_kind,
        result_protocol=fixture.TCADManagedExecution.result_protocol,config_record=dict(config),
        capabilities=vars(fixture.TCADManagedExecution.lifecycle_capabilities()))
    source=work/'work/package.json'
    source.write_bytes(fixture.canonical_json(fixture._small_resolved_execution_package().model_dump(mode='json')))
    identity=e.proof(source)
    payload=fixture.LocalFileDescriptor(name='0001-retired',local_path=str(source),
        size_bytes=identity['size_bytes'],media_type='application/json')
    owner=record/'trusted-owner.json';c.write(owner,dict(call_id=payload.name,scope=config['scope']))
    admission=fixture.ExecutionAdmission(outcome='policy',policy_digest='3'*64,policy={},
        budget=dict(max_storage_bytes=2_000_000_000,wall_time_seconds=60),
        allowance=dict(max_storage_bytes=2_000_000_000,wall_time_seconds=60),
        outside_allowance='deny',budget_key='4'*64,reason='Trusted dependency fixture; no scientific qualification')
    backend=fixture._ManagedTCADBackend(tmp_path)
    wrapper=fixture.TCADManagedExecution(config,backend,
        gate=lambda phase,**bound:dict(receipt=e.proof(owner),authorization=admission),
        input_metadata=lambda item:(2,'application/json'),open_input=lambda item:io.BytesIO(b'{}'))
    config['execution_interface']['public_capabilities']=e._public_capabilities(wrapper)
    c.write(record/'experiment-config.json',config);(record/'experiment-config.json').chmod(0o400)
    # Trusted retained host control facts include both exact projections the
    # production configure owner publishes for this attempt.
    projection=root/'host-runtime/experiment-control/001/interfaces/000.json'
    projection.parent.mkdir(parents=True,exist_ok=True)
    c.write(projection,config);projection.chmod(0o400)
    # Supply the same exact retained-native input shape as a historical call.
    # This producer is a local deterministic fixture, not the managed socket
    # prepare path and not evidence that new socket preparation is supported.
    stage=wrapper._stage(payload.name);stage.mkdir(parents=True,exist_ok=True)
    prepared=backend.prepare(payload,preparation_profile='tcad.execution-package.v2',exchange_directory=stage,
        input_metadata=lambda item:(2,'application/json'),open_input=lambda item:io.BytesIO(b'{}'))
    prepared=prepared.model_copy(update={'name':payload.name})
    prepared_original=e.proof(prepared.local_path)
    retained=dict(payload=payload.model_dump(mode='json'),
        retained_payload=dict(payload.model_dump(mode='json'),file_stat=identity['file_stat']),
        prepared=dict(prepared.model_dump(mode='json'),file_stat=prepared_original['file_stat']),
        target=wrapper.target,authority=e.proof(owner),authorization=admission.model_dump(mode='json'))
    c.write(stage/'binding.json',retained)
    research=dict(declarations=[dict(mechanism_ref='w001.retired',action='declare',question='An unsubmitted fixture question',exploration=True)],
        mechanism_ref='w001.retired',experiment_question=dict(question='Can this original be submitted?',
            change_criterion='Verified non-submission prevents interpreting an experimental result'))
    prior=worker_research.initial(config)
    state,binding=worker_research.prepare(config,prior,research)
    worker_research.publish(config,prior,state)
    # An already accepted historical original is dependency input, rather than
    # a new call admitted without today's authority/allocation requirements.
    accepted=dict(wrapper.acceptance_metadata(prepared),call_id=prepared.name,
        scope=config['scope'],native_attempt=config['native_attempt'],interface=config['execution_interface'],
        prepared=prepared.model_dump(mode='json'),script=e.proof(prepared.local_path),mechanism_binding=binding)
    directory=wrapper.records.root/'calls'/prepared.name;directory.mkdir(parents=True,exist_ok=True)
    frozen_source=directory/'source.py';frozen_source.write_bytes(Path(prepared.local_path).read_bytes())
    frozen_source.chmod(0o400);accepted['script']=e.proof(frozen_source)
    c.write(directory/'accepted.json',accepted);(directory/'accepted.json').chmod(0o400)
    assert wrapper.records.accepted(prepared.name)==accepted
    assert len(accepted['mechanism_binding'])==8
    wrapper.dispatch_lease(accepted)
    assert e.retire_original(config,wrapper,payload.name)['status']=='not_submitted'
    (directory/'submission-retirement.json').chmod(0o400)
    assert 'submit' not in backend.events
    observation=history._observe_managed_calls(root,job,call_ids=[prepared.name])
    row,=observation
    history._validate_managed(root,job,observation)
    assert row['status']=='not_submitted' and row['mechanism_binding']==accepted['mechanism_binding']
    assert row['completed'] is None and row['retirement'] is not None
    assert 'submit' not in backend.events
