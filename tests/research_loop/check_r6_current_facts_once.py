"""Current inventory ownership and publication boundaries; no model execution."""
from pathlib import Path
import json
import sys
import pytest

from scidiscovery.research_loop import contracts as c, history, checkpoint, worker_delivery
from scidiscovery.research_loop import handoff_state, accepted_budget
from scidiscovery.research_loop import semantic, budget, continuation, execution, native, configuration_updates
from scidiscovery.research_loop import execution_inputs, experiments
from scidiscovery.research_loop import backends, native_recovery, controller
from scidiscovery.research_loop import execution_resume, execution_recovery, experiment_client
from scidiscovery.research_loop.output_repair import OutputRepairRequired, WorkRepairRequired
from test_history_handoff import scene, finish


def test_consumers_use_explicit_accepted_inventory_without_observing(monkeypatch, tmp_path):
    job = dict(step=1, role='W')
    record = tmp_path / 'native-evidence/001'
    call = dict(call_id='original-call', status='known_success')
    output = dict(name='retained-original')
    inventory = c.CurrentInventory(tmp_path, job, record, 0, [call],
        {'original-call': [output]}, delivery={'accepted': True},
        checkpoint={'accepted': True})
    def forbidden(*args, **kwargs):
        pytest.fail('A consumer tried to observe or accept the node again')
    monkeypatch.setattr(history, '_observe_managed_calls', forbidden)
    monkeypatch.setattr(history, '_observe_scientific_outputs', forbidden)
    monkeypatch.setattr(worker_delivery, '_validate', forbidden)
    monkeypatch.setattr(checkpoint, '_validate', forbidden)
    for _ in range(3):
        assert history.managed_calls(inventory) == [call]
        assert history.scientific_outputs(inventory, call) == [output]
        assert worker_delivery.validate(tmp_path, job, record, inventory=inventory) == {'accepted': True}
        assert checkpoint.validate(tmp_path, job, record, inventory=inventory) == {'accepted': True}


def test_seal_accepts_before_publication_without_post_validate(scene, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Fresh seal repeated complete independent validation')
    monkeypatch.setattr(c, 'validate', forbidden)
    _, _, record = finish(scene, 1, 'H', [], action='wait')
    assert (record / 'result-identity.json').is_file()


def test_precommit_observation_gate_cannot_leave_a_seal(scene, monkeypatch):
    def refused(root, job, seal, *, inventory):
        assert not (root / 'native-evidence/001/result-identity.json').exists()
        raise ValueError('explicit observer acceptance failure')
    monkeypatch.setattr(history, 'validate_current_observation', refused)
    with pytest.raises(ValueError, match='explicit observer acceptance failure'):
        finish(scene, 1, 'H', [], action='wait')
    assert not (scene / 'native-evidence/001/result-identity.json').exists()


def test_closed_seal_read_and_independent_audit_do_not_generate(scene, monkeypatch):
    _, work, record = finish(scene, 1, 'H', [], action='wait')
    expected = c.read(record / 'result.json')
    def forbidden(*args, **kwargs):
        pytest.fail('Reading an accepted seal regenerated current inventory or projections')
    monkeypatch.setattr(c, 'accept_current_inventory', forbidden)
    monkeypatch.setattr(history, 'refresh', forbidden)
    assert c.seal_result(scene, 1) == expected
    assert c.validate(work) == expected


def test_inventory_from_another_attempt_cannot_seal(scene):
    job = c.build_job(scene, 1, 'H', [])
    stale = c.CurrentInventory(scene, job, scene / 'native-evidence/999', 0, [], {})
    with pytest.raises(ValueError, match='another task or attempt'):
        c.seal_result(scene, 1, inventory=stale)
    assert not (scene / 'native-evidence/001/result-identity.json').exists()


def test_partial_result_without_acceptance_marker_is_not_a_sealed_fact(scene):
    c.build_job(scene, 1, 'H', [])
    c.write(scene/'native-evidence/001/result.json',dict(role='H',step=1,summary='Unaccepted partial result'))
    with pytest.raises(FileNotFoundError):
        handoff_state.read_sealed(scene,1)


def test_selection_consumers_read_once_accepted_choices_and_diagnostics(scene, monkeypatch):
    job=c.build_job(scene,1,'H',[])
    selected=[dict(label='accepted input')]
    inventory=c.CurrentInventory(scene,job,scene/'native-evidence/001',0,[],{},selected_inputs=selected,
        interpretation=dict(focus=None,allocation=None,diagnostics=[]))
    def forbidden(*args,**kwargs):
        pytest.fail('A choice consumer repeated selection acceptance')
    monkeypatch.setattr(c,'_accept_selected_inputs',forbidden)
    for _ in range(3):
        assert c.selected_inputs(inventory) is selected
        assert semantic.author_choices(scene,job,{},inventory=inventory)[1]==[]
    inventory.selection_diagnostics=[dict(code='implementation_unresolved',subject='original missing choice')]
    with pytest.raises(OutputRepairRequired,match='original missing choice'):
        semantic.author_choices(scene,job,{},inventory=inventory)


def test_material_name_only_reads_the_accepted_binding(scene):
    row=dict(path='inputs/parents/001/artifacts/report.txt',source=str(scene/'missing-original'))
    assert c.material_name(scene/'project/nodes/001',row)=='artifacts/report.txt'


@pytest.fixture
def exit_scene(scene,monkeypatch):
    job=c.build_job(scene,1,'W',[])
    record=scene/'native-evidence/001'
    c.write(record/'native-outcome.json',{})
    (record/'native.jsonl').write_text('Retained native original.\n')
    monkeypatch.setattr(execution,'records',lambda *args:[record])
    monkeypatch.setattr(configuration_updates,'for_job',lambda *args:dict(resume_policy=None))
    monkeypatch.setattr(budget,'review_configuration',lambda *args:dict(mode='not_requested'))
    monkeypatch.setattr(budget,'_attempt_consumption',lambda *args,**kwargs:(10,1.))
    monkeypatch.setattr(budget,'node_limits',lambda *args:dict(max_observed_tokens=100,max_seconds=100))
    monkeypatch.setattr(budget,'snapshot',lambda *args:dict(max_tokens=100,max_seconds=100))
    monkeypatch.setattr(budget,'used_steps',lambda *args:1)
    monkeypatch.setattr(native,'usage',lambda *args:dict(observed_tokens=10,unknown_steps=[]))
    return scene,job,record


@pytest.mark.parametrize('reason',['worker_resume_budget_unavailable','worker_resume_node_exhausted'])
def test_budget_exit_does_not_search_native_sessions(exit_scene,monkeypatch,reason):
    root,job,record=exit_scene
    def forbidden(*args,**kwargs):
        pytest.fail('A budget proof tried to search native session history')
    def denied(*args,**kwargs):
        raise budget.NodeBudgetUnavailable(reason)
    monkeypatch.setattr(continuation,'session',forbidden)
    monkeypatch.setattr(budget,'accept_continuation',denied)
    proof=worker_delivery.authorize_exit(root,job,record,reason)
    assert proof['kind']=='budget' and proof['reason']==reason
    assert proof['consumption']==dict(tokens=10,seconds=1.)


@pytest.mark.parametrize('reason',['worker_resume_unsupported_backend','worker_resume_thread_unavailable','worker_resume_session_unavailable'])
def test_session_exit_still_requires_its_exact_native_diagnosis(exit_scene,monkeypatch,reason):
    root,job,record=exit_scene
    def unavailable(*args,**kwargs):
        raise continuation.ResumeUnavailable(reason)
    monkeypatch.setattr(continuation,'session',unavailable)
    proof=worker_delivery.authorize_exit(root,job,record,reason)
    assert proof['kind']=='session' and proof['reason']==reason
    assert worker_delivery.validate_exit(root,job,record,proof)==reason


def test_compiler_capture_reads_the_accepted_private_material_without_old_call_validation(scene,monkeypatch):
    job=c.build_job(scene,1,'H',[])
    path=scene/'accepted-data.json'
    path.write_text('{}')
    path.chmod(0o400)
    info=path.stat()
    material=dict(source=str(path),original_source=str(path),size_bytes=info.st_size,
        path='inputs/parents/001/artifacts/experiments/original-call/artifacts/data.json',
        file_stat=[info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns])
    def forbidden(*args,**kwargs):
        pytest.fail('Reading an accepted private material revalidated its old call')
    monkeypatch.setattr(experiments,'verify_observation',forbidden)
    assert execution_inputs.capture(scene,job,[material])==[]


def test_terminal_collector_consumes_the_supplied_current_inventory(scene,monkeypatch):
    job=c.build_job(scene,1,'H',[])
    record=scene/'native-evidence/001'
    inventory=c.CurrentInventory(scene,job,record,0,[],{})
    def forbidden(*args,**kwargs):
        pytest.fail('The same terminal collector accepted current facts twice')
    def sealed(root,step,*,inventory):
        assert inventory is accepted
        return dict(summary='Accepted once')
    accepted=inventory
    monkeypatch.setattr(c,'accept_current_inventory',forbidden)
    monkeypatch.setattr(execution,'record',lambda *args:record)
    monkeypatch.setattr(execution,'origin',lambda *args:record)
    monkeypatch.setattr(native.runtime,'node_state',lambda *args:'terminal')
    monkeypatch.setattr(budget,'prelaunch_budget_reason',lambda *args:None)
    monkeypatch.setattr(checkpoint,'enabled',lambda *args:False)
    monkeypatch.setattr(continuation,'automatic_decision',lambda *args,**kwargs:dict(action='none'))
    monkeypatch.setattr(continuation,'seal_or_repair',sealed)
    assert native.wait_native(scene,1,inventory=inventory)==dict(summary='Accepted once')


def test_output_repair_admission_receives_the_same_current_inventory(scene,monkeypatch):
    job=c.build_job(scene,1,'H',[])
    record=scene/'native-evidence/001'
    inventory=c.CurrentInventory(scene,job,record,0,[],{})
    class RepairReached(Exception):pass
    def malformed(*args,**kwargs):
        raise OutputRepairRequired('invalid_output','Original retained response requires repair')
    def request(root,step,last,*,inventory):
        assert inventory is accepted
        raise RepairReached
    accepted=inventory
    monkeypatch.setattr(execution,'records',lambda *args:[record])
    monkeypatch.setattr(execution,'origin',lambda *args:record)
    monkeypatch.setattr(native.runtime,'node_state',lambda *args:'terminal')
    monkeypatch.setattr(c,'native_outcome',lambda *args:dict(returncode=0,stop_reason=None))
    monkeypatch.setattr(c,'seal_result_from_inventory',malformed)
    monkeypatch.setattr(checkpoint,'enabled',lambda *args:False)
    monkeypatch.setattr(continuation,'validate_launcher',lambda *args:dict(output_repair=True))
    monkeypatch.setattr(continuation,'output_request',request)
    with pytest.raises(RepairReached):
        continuation.start(scene,1,inventory=inventory)


@pytest.mark.parametrize('status',['unknown','not_submitted','known_success'])
def test_recovery_owner_accepts_only_the_changed_original_call(scene,monkeypatch,status):
    job=c.build_job(scene,1,'W',[])
    row=dict(call_id='changed-original',status=status)
    seen=[]
    def observe(root,current,*,call_ids,context):
        assert root==scene and current is job and call_ids==['changed-original']
        seen.append('observe')
        return [row]
    def accept(root,current,rows,*,context):
        assert rows==[row]
        seen.append('accept')
    def forbidden(*args,**kwargs):
        pytest.fail('Original-call recovery rebuilt the closed scientific node')
    monkeypatch.setattr(c,'accept_current_inventory',forbidden)
    monkeypatch.setattr(history,'_observe_managed_calls',observe)
    monkeypatch.setattr(history,'_validate_managed',accept)
    accepted=c.accept_recovery_call(scene,job,'changed-original')
    assert accepted is row and seen==['observe','accept']
    monkeypatch.setattr(history,'_observe_managed_calls',forbidden)
    monkeypatch.setattr(history,'_validate_managed',forbidden)
    monkeypatch.setattr(experiment_client,'record_directory',lambda *args:scene/'original-records')
    monkeypatch.setattr(budget,'historical_step_limit',lambda *args:1)
    monkeypatch.setattr(execution_recovery,'prefix',lambda *args:[])
    monkeypatch.setattr(execution_recovery,'call_view',lambda *args,**kwargs:{})
    published=[]
    monkeypatch.setattr(execution_recovery,'append',lambda root,base,phase,**kwargs:published.append((phase,kwargs['row'])))
    execution_resume._publish(scene,job,dict(call_id='changed-original'),None,row=row,query=False)
    expected='terminal' if status=='known_success' else 'not_submitted' if status=='not_submitted' else 'accepted'
    assert published==[(expected,row)]


def test_closed_scientific_node_cannot_be_observed_as_current_again(scene,monkeypatch):
    finish(scene,1,'H',[],action='wait')
    def forbidden(*args,**kwargs):
        pytest.fail('A closed node was observed again')
    monkeypatch.setattr(history,'_observe_managed_calls',forbidden)
    with pytest.raises(ValueError,match='Sealed scientific facts'):
        c.accept_current_inventory(scene,1)


def test_direct_work_repair_failure_retains_its_explicit_current_inventory(scene,monkeypatch):
    job=c.build_job(scene,1,'W',[])
    record=scene/'native-evidence/001'
    inventory=c.CurrentInventory(scene,job,record,0,[],{})
    monkeypatch.setattr(worker_delivery,'assessment',lambda *args,**kwargs:
        dict(status='incomplete',missing=['h001.missing']))
    with pytest.raises(WorkRepairRequired) as failure:
        worker_delivery.require_delivery(scene,job,{},record,inventory=inventory)
    assert failure.value.inventory is inventory


@pytest.mark.parametrize('fault',['malformed_json','layout_collision'])
def test_owner_output_diagnosis_returns_to_same_repair_without_accepting_again(scene,monkeypatch,fault):
    original_seal=c.seal_result
    monkeypatch.setattr(c,'seal_result',lambda *args,**kwargs:None)
    job,work,record=finish(scene,1,'H',[],action='wait')
    monkeypatch.setattr(c,'seal_result',original_seal)
    if fault=='malformed_json':
        (record/'science-output.json').write_text('{broken')
        outcome=c.read(record/'native-outcome.json')
        c.write(record/'native-outcome.json',dict(outcome,raw_ref=c.file_ref(record/'science-output.json')))
    else:
        def collision(*args,**kwargs):
            raise OutputRepairRequired('deliverable_path_collision','Remove the redundant author-owned copy')
        monkeypatch.setattr(c,'_material_inventory',collision)
    inventory=c.accept_current_inventory(scene,1)
    assert isinstance(inventory.output_error,OutputRepairRequired)
    if fault=='layout_collision':assert inventory.output_error.code=='deliverable_path_collision'
    def forbidden(*args,**kwargs):
        pytest.fail('Output repair repeated the owner acceptance or layout diagnosis')
    monkeypatch.setattr(c,'accept_current_inventory',forbidden)
    monkeypatch.setattr(c,'_material_inventory',forbidden)
    resume_policy=dict(inventory.policy.get('resume_policy') or {},output_repair=True)
    monkeypatch.setattr(inventory,'policy',dict(inventory.policy,resume_policy=resume_policy))
    def repair(root,step,*,inventory):
        assert inventory is accepted
        return continuation.output_request(root,step,record,inventory=inventory,verify_task=False)
    accepted=inventory
    monkeypatch.setattr(continuation,'start',repair)
    request=continuation.seal_or_repair(scene,1,inventory=inventory)
    assert request['code']==inventory.output_error.code
    assert request['raw_ref']==c.file_ref(record/'science-output.json')
    assert not (record/'result-identity.json').exists()


def test_missing_scientific_output_keeps_native_checkpoint_without_input_or_draft_acceptance(scene,monkeypatch):
    monkeypatch.setattr(c,'seal_result',lambda *args,**kwargs:None)
    job,work,record=finish(scene,1,'W',[])
    (record/'science-output.json').unlink()
    outcome=c.read(record/'native-outcome.json')
    c.write(record/'native-outcome.json',dict(outcome,output_status='missing',raw_ref=None))
    monkeypatch.setattr(checkpoint,'enabled_for_policy',lambda *args,**kwargs:True)
    def forbidden(*args,**kwargs):
        pytest.fail('Missing science triggered draft, scientific-input or qualification acceptance')
    monkeypatch.setattr(c,'_validate_inputs_from_facts',forbidden)
    monkeypatch.setattr(c,'_material_inventory',forbidden)
    inventory=c.accept_current_inventory(scene,1)
    assert not inventory.science_available
    assert inventory.checkpoint['science']==dict(status='missing',diagnostic='missing_output')
    assert inventory.checkpoint['binding']['stage_owner']==c.record_ref(scene/'jobs/001.json')
    assert 'science-output.json' in inventory.checkpoint['missing_native_originals']
    assert not (record/'result-identity.json').exists()


@pytest.mark.parametrize('kind',['observation','recovery','old_h'])
def test_worker_reads_only_the_required_frozen_terminal_pointer(scene,monkeypatch,kind):
    from scidiscovery.research_loop import originals
    origin=originals.local_identity(scene)
    target=dict(mechanism_ref='h001.m1',source_h=1,source_h_ref=dict(path=str(scene/'old-H-seal.json')))
    binding=dict(target,job_ref=dict(path=str(scene/'jobs/002.json')),grant=None)
    row=dict(call_id='retained-original',status='known_failed',mechanism_binding=binding)
    observation=scene/'host-history/observations/002.json'
    c.write(observation,dict(managed_calls=[dict(call_id='unrelated'),row]))
    pointer=dict(source_step=2,origin_identity=origin,observation_ref=dict(path='host-history/observations/002.json'),
        call_index=1,call_id=row['call_id'],mechanism_binding=binding,recovery_ref=None)
    recovery=scene/'host-history/execution-recoveries/002/retained-original/000001.json'
    if kind=='recovery':
        c.write(recovery,dict(row=row))
        pointer['recovery_ref']=dict(origin_identity=origin,path=recovery.relative_to(scene).as_posix())
    if kind=='old_h':
        target=dict(target,source_h=9,source_h_ref=dict(path=str(scene/'new-H-seal.json')))
    snapshot=scene/'frozen-worker-table.json'
    c.write(snapshot,dict(table=dict(mechanisms={'h001.m1':dict(last_terminal=pointer),
        'h001.unrelated':dict(last_terminal=dict(observation_ref=dict(path='must-not-read.json')))})))
    job=dict(role='W',search=dict(focus=dict(action='screen'),coverage_obligation=[target]),
        history=dict(breadth_snapshot=dict(origin_identity=origin,path=snapshot.name)))
    def forbidden(*args,**kwargs):pytest.fail('Worker delivery reopened the full historical prefix')
    monkeypatch.setattr(history,'_source',forbidden)
    monkeypatch.setattr(history,'read_observation',forbidden)
    read=c.read;seen=[]
    def observed(path):
        if Path(path) in {snapshot,observation,recovery}:seen.append(Path(path))
        return read(path)
    monkeypatch.setattr(c,'read',observed)
    result=list(worker_delivery._prefix_calls(scene,job))
    if kind=='old_h':
        assert result==[] and seen==[snapshot]
    else:
        assert len(result)==1 and result[0][0].step==2 and result[0][1]==row
        assert seen==[snapshot,recovery if kind=='recovery' else observation]


def test_inherited_worker_evidence_preserves_each_explicit_proof_issuer():
    from types import SimpleNamespace
    original=dict(goal_id='original',settings_ref=dict(path='/original/settings.json'))
    issuer=dict(goal_id='recovery-issuer',settings_ref=dict(path='/issuer/settings.json'))
    proof=dict(path='accepted.json',snapshot='objects/accepted.json',origin_identity=issuer)
    row=dict(mechanism_binding=dict(mechanism_ref='h001.m1'),accepted=proof,completed=proof,
        terminal=proof,observation=proof,originals=[proof],outputs=[proof])
    value=worker_delivery._evidence(SimpleNamespace(inherited=True,origin_identity=original),row)
    assert all(value[key]['origin_identity']==issuer for key in ('accepted','completed','terminal','observation'))
    assert value['originals'][0]['origin_identity']==issuer and value['outputs'][0]['origin_identity']==issuer


def test_host_direct_source_reads_the_bound_original_without_a_native_alias(scene,monkeypatch):
    finish(scene,1,'H',[],action='wait')
    job=c.build_job(scene,2,'A',[1])
    alias=c.workspace(scene,2)/job['direct_refs'][0]['path']
    assert not alias.exists()
    def forbidden(*args,**kwargs):pytest.fail('Reading a direct accepted input requalified its producer')
    monkeypatch.setattr(c,'validate',forbidden)
    monkeypatch.setattr(c,'validate_original',forbidden)
    rows=history.direct_sources(scene,job)
    assert len(rows)==1 and rows[0][0]['step']==1
    assert rows[0][2]['raw_ref']['size_bytes']==job['direct_originals'][0]['size_bytes']
    assert not alias.exists()


def test_input_validation_rejects_a_caller_changed_search_before_consuming_accepted_job(scene):
    job=c.build_job(scene,1,'H',[])
    changed=dict(job,search=dict(focus=dict(mechanism_ref='invented',action='screen')))
    with pytest.raises(ValueError,match='accepted canonical job'):
        c._validate_inputs(scene,c.workspace(scene,1),changed)


def test_current_input_validation_reads_its_frozen_policy_once(scene,monkeypatch):
    job=c.build_job(scene,1,'H',[])
    select=c.settings_for_job
    reads=[]
    def observed(root,current,*,base,head):
        if current is job and sys._getframe(1).f_code is c._validate_inputs.__code__:
            reads.append(current)
        return select(root,current,base=base,head=head)
    monkeypatch.setattr(c,'settings_for_job',observed)
    c._validate_inputs(scene,c.workspace(scene,1),job)
    assert reads==[job]


def _forbid_consumer_policy_reads(monkeypatch):
    settings=c.settings
    consumers={function.__code__ for function in (
        c.material_inventory,history._inventory_policy,history.capture,
        history._capture,history._capture.__wrapped__,history.observer_input_sources,
        history.validate_observation)}
    def observed(root,job=None):
        # Observe direct frozen-policy reads only. Real protocol, live-head and
        # native-receipt checks keep reading their own current settings.
        if sys._getframe(1).f_code in consumers:
            pytest.fail('An inventory consumer reread the frozen settings')
        return settings(root,job)
    monkeypatch.setattr(c,'settings',observed)


def test_inventory_material_and_observer_consumers_reuse_the_frozen_policy(scene,monkeypatch):
    monkeypatch.setattr(c,'seal_result',lambda *args,**kwargs:None)
    job,work,record=finish(scene,1,'H',[],action='wait')
    inventory=c.accept_current_inventory(scene,1)
    _forbid_consumer_policy_reads(monkeypatch)
    assert c.material_inventory(scene,1,inventory=inventory)[0]==inventory.materials
    assert history.capture(scene,job,{},inventory.materials,inventory=inventory) is None
    assert history._capture(scene,job,{},inventory.materials,inventory=inventory) is None
    raw_ref=c.file_ref(record/'science-output.json')
    config=dict(adapter_ref='registered-originals-v1')
    snapshot=dict(binding=history._observer_binding(raw_ref,config),candidates=[],managed_calls=[])
    monkeypatch.setattr(history,'read_observation',lambda *args:snapshot)
    monkeypatch.setattr(history,'_managed_config_for_inventory',lambda *args:config)
    assert history.validate_observation(scene,job,dict(raw_ref=raw_ref),inventory=inventory) is None


@pytest.mark.parametrize('changed',['root','job','revision','attempt'])
def test_inventory_policy_cannot_cross_another_job_revision_or_attempt(scene,monkeypatch,changed):
    job=c.build_job(scene,1,'H',[])
    record=scene/'native-evidence/001'
    inventory=c.CurrentInventory(scene,job,record,0,[],{},policy=c.settings(scene,job))
    if changed=='root':inventory.root=scene/'another-instance'
    elif changed=='job':inventory.job=dict(job,step=2)
    elif changed=='revision':inventory.job=dict(job,configuration_revision=dict(
        revision=1,path='host-control/configuration/revisions/000001.json'))
    else:inventory.record=record/'continuations/001'
    _forbid_consumer_policy_reads(monkeypatch)
    with pytest.raises(ValueError,match='another task or attempt'):
        c.material_inventory(scene,1,inventory=inventory)
    if changed!='attempt':
        with pytest.raises(ValueError,match='another task or attempt'):
            history.capture(scene,job,{},[],inventory=inventory)


@pytest.mark.parametrize('change',['budget','native','recovery'])
def test_next_configuration_operation_reads_after_accepted_state_changes(scene,monkeypatch,change):
    job=c.build_job(scene,1,'E',[])
    read=accepted_budget.read
    observed=[]
    def fresh(root):
        state=read(root)
        observed.append(state)
        return state
    monkeypatch.setattr(accepted_budget,'read',fresh)
    policy=configuration_updates.current(scene)
    assert len(observed)==1
    before=observed[0]
    if change=='budget':
        budget.amend(scene,max_tokens=before['ceilings']['max_tokens']+1000,
            authorization_note='Explicit adjacent-operation fixture amendment.')
    elif change=='native':
        record=c.host_record(c.workspace(scene,1))
        accepted_budget.accept_native(scene,1,record,dict(task_ref=c.record_ref(scene/'jobs/001.json')))
    else:
        recovery=scene/'execution-authority/calls/fixture/recovery-operations/000001.json'
        recovery.parent.mkdir(parents=True)
        c.write(recovery,dict(step=1,call_id='fixture',status='unknown'))
        snapshot=recovery.parent/'snapshots/000001.json'
        snapshot.parent.mkdir()
        c.write(snapshot,before['search'])
        snapshot.chmod(0o400)
        reference=dict(origin_identity=before.prefix['branch'],
            path=snapshot.relative_to(scene).as_posix(),size_bytes=snapshot.stat().st_size)
        accepted_budget.publish_recovery(scene,step=1,call_id='fixture',recovery_ref=c.record_ref(recovery),
            search_delta=dict(summary=before['search'],rows={}),search_ref=reference)
    observed.clear()
    assert configuration_updates.current(scene)==policy
    assert len(observed)==1 and observed[0]['seq']>before['seq']
    after=observed[0]
    if change=='budget':assert after['ceilings']['max_tokens']==before['ceilings']['max_tokens']+1000
    elif change=='native':assert after['current_native']['step']==job['step']
    else:assert after.row('execution-recovery/001/fixture')['ref']==c.record_ref(recovery)


def test_configuration_head_is_shared_locally_and_job_revision_stays_exact(scene,monkeypatch):
    from test_configuration_updates import edit
    edit(scene,model='fixture-first')
    read=accepted_budget.read
    observed=[]
    def fresh(root):
        state=read(root)
        observed.append(state)
        return state
    monkeypatch.setattr(accepted_budget,'read',fresh)
    assert configuration_updates.current(scene)['model']=='fixture-first'
    assert len(observed)==1
    observed.clear()
    assert configuration_updates.for_new_job(scene,1)['model']=='fixture-first'
    assert len(observed)==1
    first=configuration_updates.bind_new_job(scene,1)
    observed.clear()
    assert configuration_updates.for_new_job(scene,1)['model']=='fixture-first'
    assert len(observed)==1
    job=c.build_job(scene,1,'H',[])
    assert job['configuration_revision']==first
    edit(scene,model='fixture-second')
    observed.clear()
    assert configuration_updates.current(scene)['model']=='fixture-second'
    assert len(observed)==1
    observed.clear()
    assert configuration_updates.for_job(scene,job)['model']=='fixture-first'
    assert len(observed)==1


def test_current_owner_reads_each_native_original_once_and_seal_consumes_it(scene,monkeypatch):
    seal=c.seal_result
    monkeypatch.setattr(c,'seal_result',lambda *args,**kwargs:None)
    job,work,record=finish(scene,1,'H',[],action='wait')
    monkeypatch.setattr(c,'seal_result',seal)
    originals={scene/'jobs/001.json',record/'native-intent.json',record/'native-outcome.json'}
    read=c.read;science=c.read_scientific_output
    reads={path:0 for path in originals};science_reads=[]
    def observed(path):
        path=Path(path)
        if path in reads:reads[path]+=1
        return read(path)
    def observed_science(path):
        if Path(path)==record/'science-output.json':science_reads.append(Path(path))
        return science(path)
    monkeypatch.setattr(c,'read',observed)
    monkeypatch.setattr(c,'read_scientific_output',observed_science)
    inventory=c.accept_current_inventory(scene,1)
    assert reads=={path:1 for path in originals}
    assert science_reads==[record/'science-output.json']
    assert inventory.payload is not None
    assert inventory.head['current_native'] is None
    def consumed(path):
        if Path(path) in originals:pytest.fail('A consumer reopened an accepted current native fact')
        return read(path)
    def forbidden(*args,**kwargs):pytest.fail('A consumer rebuilt the accepted chain or scientific payload')
    monkeypatch.setattr(c,'read',consumed)
    monkeypatch.setattr(c,'science_payload',forbidden)
    monkeypatch.setattr(execution,'accepted_intents',forbidden)
    monkeypatch.setattr(execution,'records',forbidden)
    monkeypatch.setattr(execution,'record',forbidden)
    monkeypatch.setattr(execution,'reference',forbidden)
    c._material_inventory(scene,1,inventory=inventory)
    assert seal(scene,1,inventory=inventory)['summary']=='A contribution'
    assert (record/'result-identity.json').is_file()


@pytest.mark.parametrize('fault',['valid','missing','malformed'])
def test_current_checkpoint_reuses_one_real_stop_and_science_diagnosis(scene,monkeypatch,fault):
    monkeypatch.setattr(c,'seal_result',lambda *args,**kwargs:None)
    job,work,record=finish(scene,1,'W',[])
    raw=record/'science-output.json'
    if fault=='missing':
        raw.unlink()
        outcome=c.read(record/'native-outcome.json')
        c.write(record/'native-outcome.json',dict(outcome,output_status='missing',raw_ref=None))
    elif fault=='malformed':
        raw.write_text('{broken')
        outcome=c.read(record/'native-outcome.json')
        c.write(record/'native-outcome.json',dict(outcome,raw_ref=c.file_ref(raw)))
    monkeypatch.setattr(checkpoint,'enabled_for_policy',lambda *args,**kwargs:True)
    stopped=worker_delivery._writers_stopped;calls=[]
    def once(*args,**kwargs):
        calls.append(args[2])
        return stopped(*args,**kwargs)
    def forbidden(*args,**kwargs):pytest.fail('A checkpoint consumer repeated the stop or science loader')
    monkeypatch.setattr(worker_delivery,'_writers_stopped',once)
    monkeypatch.setattr(worker_delivery,'writers_stopped',forbidden)
    monkeypatch.setattr(checkpoint,'_binding',forbidden)
    monkeypatch.setattr(checkpoint,'_science',forbidden)
    inventory=c.accept_current_inventory(scene,1)
    assert calls==[record]
    expected='present' if fault=='valid' else 'missing' if fault=='missing' else 'invalid'
    assert inventory.checkpoint['science']['status']==expected
    if fault=='malformed':
        assert inventory.payload is None and inventory.science_error is inventory.output_error
        assert inventory.checkpoint['science']['diagnostic']==inventory.science_error.code


def test_public_seal_rejects_inventory_after_a_new_attempt_without_rescanning_chain(scene,monkeypatch):
    seal=c.seal_result
    monkeypatch.setattr(c,'seal_result',lambda *args,**kwargs:None)
    job,work,record=finish(scene,1,'H',[],action='wait')
    inventory=c.accept_current_inventory(scene,1)
    successor=record/'continuations/001'
    successor.mkdir(parents=True)
    intent=dict(task_ref=inventory.task_ref,attempt=1,step=1,continuation_kind='repair',
        root_intent_ref=c.record_ref(record/'native-intent.json'),previous=execution.proof(record))
    state=accepted_budget.accept_native(scene,1,successor,intent)
    assert state.current_job['native_record']==successor.relative_to(scene).as_posix()
    def forbidden(*args,**kwargs):pytest.fail('Stale-inventory admission rescanned the accepted chain')
    monkeypatch.setattr(execution,'accepted_intents',forbidden)
    monkeypatch.setattr(execution,'records',forbidden)
    monkeypatch.setattr(execution,'record',forbidden)
    with pytest.raises(ValueError,match='another task or attempt'):
        seal(scene,1,inventory=inventory)
    with pytest.raises(ValueError,match='another task or attempt'):
        c.material_inventory(scene,1,inventory=inventory)
    assert not (record/'result-identity.json').exists()


def test_reconciled_terminal_owner_and_public_loader_each_read_usage_once(scene,monkeypatch):
    monkeypatch.setattr(c,'seal_result',lambda *args,**kwargs:None)
    job,work,record=finish(scene,1,'H',[],action='wait')
    raw=record/'science-output.json'
    text=raw.read_text()
    raw.rename(record/'result.pending.json')
    thread='11111111-2222-4333-8444-000000000001'
    meter=dict(input_tokens=103,cached_input_tokens=80,output_tokens=17)
    events=[dict(type='thread.started',thread_id=thread),dict(type='turn.started'),
        dict(type='error',message='Reconnecting... 2/5'),
        dict(type='item.completed',item=dict(type='agent_message',text=text)),
        dict(type='turn.completed',usage=meter)]
    backend=backends.selected(dict(native_launcher_argv=['codex']))
    failures=[backend.event(event)['failure'] for event in events if backend.event(event).get('failure') is not None]
    c.write(record/'native-outcome.json',dict(returncode=0,native_started=True,
        cleanup='observed_group_cleaned',stop_reason='native_failure_event',native_backend='codex',
        terminal_observed=True,native_failure=native_recovery.combine(failures),
        native_activity_observed=True,thread_id=thread,
        usage=dict(**meter,observed_tokens=120,unknown_steps=[1]),
        metering=dict(coverage='unknown',reason='native_failure_event',tokens=meter,live_reason=None)))
    c.write(record/'usage.json',meter)
    (record/'native.jsonl').write_text(''.join(json.dumps(event)+'\n' for event in events))
    for name,pid in [('native-process.json',999999998),('runner-process.json',999999999)]:
        c.write(record/name,dict(task_ref=c.record_ref(scene/'jobs/001.json'),pid=pid,pgid=pid,sid=pid,
            start_ticks=1,boot_id='retained-original-boot'))
    read=c.read;observed=[]
    def counted(path):
        if Path(path)==record/'usage.json':observed.append(Path(path))
        return read(path)
    monkeypatch.setattr(c,'read',counted)
    inventory=c.accept_current_inventory(scene,1)
    assert inventory.native_outcome['stop_reason'] is None
    assert inventory.native_outcome['metering']['coverage']=='terminal'
    assert raw.samefile(record/'result.pending.json')
    assert observed==[record/'usage.json']
    observed.clear()
    assert c.native_outcome(record)['stop_reason'] is None
    assert observed==[record/'usage.json']


def test_observer_snapshot_write_failure_discards_accepted_inputs_with_fallback(tmp_path,monkeypatch):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Retain exact observed source contributions.')
    adapter=tmp_path/'observer.py';adapter.write_text('# Trusted adapter fixture; never executed\n')
    launcher=tmp_path/'codex';launcher.write_text('# Synthetic identity only; never executed\n');launcher.chmod(0o700)
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[str(launcher)],max_tokens=1000,max_seconds=60,max_steps=5,
        model='fixture',reasoning_effort='high',search_policy=None,experiment_mode='external',
        formal_review_policy={'version':1},source_restrictions=[dict(source=str(objective),restriction='clear')],
        history_observer=dict(adapter=str(adapter),config={},source_contract=execution_inputs.SOURCE_CONTRACT))
    seal=c.seal_result
    monkeypatch.setattr(c,'seal_result',lambda *args,**kwargs:None)
    job,work,record=finish(root,1,'H',[],action='wait',files={'source.txt':'An observed scientific source.'})
    monkeypatch.setattr(c,'seal_result',seal)
    thread='11111111-2222-4333-8444-000000000001'
    home=tmp_path/'identity';session=home/'sessions'/(thread+'.jsonl');session.parent.mkdir(parents=True)
    session.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
    (record/'native.jsonl').write_text(json.dumps(dict(type='thread.started',thread_id=thread))+'\n')
    outcome=c.read(record/'native-outcome.json')
    c.write(record/'native-outcome.json',dict(outcome,thread_id=thread,
        native_identity=dict(participant_scope='codex_exec_single_agent_v1',home=str(home),path=str(session))))
    def observe(config,request):
        origins=[row['origin'] for row in request['input_sources']['available'] if row['origin']['kind']=='producer']
        return dict(candidates=[],current_candidate=None,actual_submission=None,
            coverage=dict(status='complete',note='Display coverage alone is not source coverage.'),
            input_sources=dict(version=1,complete=True,sources=origins))
    monkeypatch.setattr(history,'read_adapter',observe)
    accept=history._observer_input_sources_for_inventory;accepted=[]
    def observed_acceptance(*args,**kwargs):
        result=accept(*args,**kwargs)
        accepted.append(result)
        return result
    monkeypatch.setattr(history,'_observer_input_sources_for_inventory',observed_acceptance)
    path=root/'host-history/observations/001.json'
    write=c.write;writes=[]
    def fail_once(destination,value):
        if Path(destination)==path:
            writes.append(value['coverage']['status'])
            if len(writes)==1:raise OSError('Synthetic first snapshot publication failure')
        return write(destination,value)
    monkeypatch.setattr(c,'write',fail_once)
    inventory=c.accept_current_inventory(root,1)
    seal(root,1,inventory=inventory)
    assert len(accepted)==1 and accepted[0]['complete'] is True
    assert writes==['complete','unavailable']
    assert inventory.observation_document==c.read(path)
    assert inventory.observation_document['coverage']['status']=='unavailable'
    assert inventory.observation_inputs is None and inventory.additional_inputs
