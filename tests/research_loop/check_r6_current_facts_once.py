"""Current inventory ownership and publication boundaries; no model execution."""
from pathlib import Path
import pytest

from scidiscovery.research_loop import contracts as c, history, checkpoint, worker_delivery
from scidiscovery.research_loop import handoff_state
from scidiscovery.research_loop import semantic, budget, continuation, execution, native, configuration_updates
from scidiscovery.research_loop import execution_inputs, experiments
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
    monkeypatch.setattr(history, 'validate_observation', refused)
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
    inventory=c.CurrentInventory(scene,job,scene/'native-evidence/001',0,[],{},selected_inputs=selected)
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
    monkeypatch.setattr(c,'seal_result',malformed)
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
        monkeypatch.setattr(c,'material_inventory',collision)
    inventory=c.accept_current_inventory(scene,1)
    assert isinstance(inventory.output_error,OutputRepairRequired)
    if fault=='layout_collision':assert inventory.output_error.code=='deliverable_path_collision'
    def forbidden(*args,**kwargs):
        pytest.fail('Output repair repeated the owner acceptance or layout diagnosis')
    monkeypatch.setattr(c,'accept_current_inventory',forbidden)
    monkeypatch.setattr(c,'material_inventory',forbidden)
    monkeypatch.setattr(configuration_updates,'for_step',lambda *args:dict(resume_policy=dict(output_repair=True)))
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
    monkeypatch.setattr(checkpoint,'enabled',lambda *args:True)
    def forbidden(*args,**kwargs):
        pytest.fail('Missing science triggered draft, scientific-input or qualification acceptance')
    monkeypatch.setattr(c,'_validate_inputs',forbidden)
    monkeypatch.setattr(c,'material_inventory',forbidden)
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
