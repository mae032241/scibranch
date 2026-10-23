"""Fork consumers retain exact candidate evidence without replaying effects."""
import shutil

import pytest

from execution_fixtures import execution_registry
from test_composed_execution_results import composed_registry

from scidiscovery.research_loop import (budget, candidate_refs, contracts as c,
    controller, delivery, fork_service, history, lineage, originals, shortlist)
from test_history_handoff import finish


OBSERVER = '''import json,sys
request=json.load(sys.stdin);step=request['step']
path='project/nodes/002/artifacts/evidence.txt'
candidates=[]
if step in (2,5):
 candidates=[dict(id='original' if step==2 else 'suffix-only',label='Fixture evidence',
  implementation=None,evaluation_key='fixture',metric=1,direction='max',status='ok',
  evidence=[path])]
actual=dict(candidate_id='original',artifact=path,evidence=[path]) if step==3 else None
print(json.dumps(dict(candidates=candidates,current_candidate=None,actual_submission=actual,
 coverage=dict(status='complete',note='Mechanical fixture only'))))
'''


@pytest.fixture
def inherited_candidates(tmp_path):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Keep candidate origins and historical effects separate.')
    observer=tmp_path/'observer.py';observer.write_text(OBSERVER)
    parent=tmp_path/'parent'
    controller.prepare(parent,objective,public,['no-model'],max_tokens=10000,max_seconds=120,
        max_steps=10,model='fixture',reasoning_effort='high',search_policy=None,
        history_observer=dict(adapter=str(observer),config={}))
    for step,role,parents,action in [(1,'H',[],'experiment'),(2,'W',[1],'wait'),
            (3,'A',[1,2],'revise_h'),(4,'H',[3],'experiment'),(5,'W',[4],'wait'),
            (6,'A',[4,5],'finish')]:
        _,_,record=finish(parent,step,role,parents,action=action,
            files={'evidence.txt':'Retained original candidate bytes.\n'} if step==2 else None)
        c.write(record/'native-outcome.json',dict(c.read(record/'native-outcome.json'),native_started=False))
    with controller._writer(parent):
        pass
    child=tmp_path/'child';limits=dict(max_tokens=1000,max_seconds=30,max_steps=2)
    preview=fork_service.preview(parent,4,child,budget=limits)
    fork_service.create(parent,4,child,budget=limits,expected_preview_id=preview['preview_id'],
        operation_id='candidate-consumers')
    owner=originals.node(child,2)
    identity=candidate_refs.origin_candidate(owner.origin_identity,'original')
    shutil.rmtree(parent)
    return child,owner,identity


def test_catalogue_and_shortlist_keep_cutoff_and_origin_after_parent_removal(inherited_candidates,monkeypatch):
    root,_,identity=inherited_candidates
    sources=history.source_sequence(root,4)
    receipts=[dict(step=job['step'],seal_sha256=sha) for job,_,_,sha in sources]
    # This test reads a directory; it cannot invoke or qualify an adapter.
    monkeypatch.setattr(delivery,'_adapter',lambda *args: {})
    catalogue=delivery.catalogue_sources(root,receipts)
    assert catalogue['items']==[dict(candidate_id=identity,status='available',source_step=2)]
    assert delivery.rebind_sources(root,dict(source_nodes=receipts,
        items=[dict(candidate_id='original',status='confirmed')],omitted_count=0))==catalogue
    members=shortlist._members(root,dict(source_cutoff=2),before_step=4)
    assert [row['candidate_id'] for row in members['members']]==[identity]
    projection=lineage.project(root,4,source_cutoff=2)
    assert projection['candidates']['actual_submission'] is None
    historical=lineage.project(root,4)['candidates']['actual_submission']
    assert historical['candidate_id']==identity and historical['observed_at']==3
    assert budget.closeout_state(root)['actions']==[]
    assert not (root/'host-delivery/resolved').exists()
    assert not (root/'jobs/002.json').exists()


def test_inherited_delivery_proof_rejects_changed_copied_original(inherited_candidates):
    root,owner,_=inherited_candidates
    job,_,seal,_=history._source(root,2)
    fact=history.read_observation(root,job,seal)
    evidence=fact['candidates'][0]['evidence'][0]
    proof=dict(path=evidence['snapshot'],sha256=evidence['sha256'],size_bytes=evidence['size_bytes'])
    path=delivery._proof(root,proof,source=owner)
    assert path.read_text()=='Retained original candidate bytes.\n'
    path.chmod(0o600);path.write_text('Changed copied evidence.\n')
    with pytest.raises(ValueError,match='Original bytes changed'):
        delivery._proof(root,proof,source=owner)


@pytest.mark.process_e2e
def test_managed_candidate_resolution_uses_retained_inputs_after_parent_removal(tmp_path,execution_registry):
    from scidiscovery.research_loop import experiment_client, experiments, generic_results
    from test_breadth_science import a, findings, h, prepare, worker
    root=prepare(tmp_path)
    h(root,labels=('P',))
    work=c.workspace(root,2)/'work'
    _,config=worker(root,work,2,[1])
    path=work/'config-2.json'
    config=experiment_client.resolve(path,config)
    experiment=work/'experiment';experiment.mkdir()
    source=experiment/'candidate.py'
    program=('import json,pathlib\n'
        'pathlib.Path("artifacts/model.bin").write_bytes(b"exact original output")\n'
        'pathlib.Path("artifacts/result.json").write_text(json.dumps(dict(version=1,metrics=[], '
        'outputs=[dict(path="artifacts/model.bin")],candidates=[dict(artifact="artifacts/model.bin")])))\n')
    source.write_text(program)
    result=experiments.dispatch(config,source,new=True,mechanism='P',
        command_options=dict(experiment_directory=str(experiment),arguments=[]))
    record=experiments.Records(config).directory(result['call_id'])
    assert result['status']=='known_success',(result,(record/'stderr.raw').read_text()[-2048:])
    finish(root,2,'W',[1],mechanism_findings=findings('P'),preserve_native_intent=True)
    a(root,3,[1,2],action='finish')
    for step in (1,2,3):
        outcome=root/'native-evidence'/f'{step:03d}'/'native-outcome.json'
        c.write(outcome,dict(c.read(outcome),native_started=False))
    with controller._writer(root):
        pass
    child=tmp_path/'child';limits=dict(max_tokens=1000,max_seconds=30,max_steps=1)
    preview=fork_service.preview(root,3,child,budget=limits)
    fork_service.create(root,3,child,budget=limits,expected_preview_id=preview['preview_id'],
        operation_id='retained-managed-candidate')
    shutil.rmtree(root)
    job,_,seal,_=history._source(child,2)
    fact=history.read_observation(child,job,seal);candidate=fact['candidates'][0]['id']
    owner=originals.node(child,2)
    resolved=generic_results.sources(child,fact,candidate,source=owner)
    assert resolved['candidate_id']==candidate and resolved['status']=='audit_pending'
    assert delivery._proof(child,resolved['objects']['artifact'],source=owner).read_bytes()==b'exact original output'
    assert any(delivery._proof(child,proof,source=owner).read_text()==program
        for label,proof in resolved['objects'].items() if label.startswith('inputs/'))
    assert not (child/'host-delivery').exists()


@pytest.mark.parametrize('foreign_id',[False,True])
def test_adapter_resolution_uses_original_id_and_rejects_foreign_reply(inherited_candidates,monkeypatch,foreign_id):
    root,_,identity=inherited_candidates
    sources=history.source_sequence(root,4)
    receipts=[dict(step=job['step'],seal_sha256=sha) for job,_,_,sha in sources]
    settings=c.settings(root)
    monkeypatch.setattr(delivery,'_settings',lambda *args: dict(settings,
        delivery_policy=dict(cleanup_seconds=5)))
    monkeypatch.setattr(delivery,'_adapter',lambda *args,**kwargs: dict(inputs=[],source_roots=[str(root)],readonly_inputs_version=1,
        config={},target={},capabilities=[]))
    def resolve(spec,request,*args,**kwargs):
        assert request['phase']=='resolve'
        assert [row['candidate_id'] for row in request['candidates']]==['original']
        # An unavailable transport response proves identity plumbing only.
        return dict(items=[dict(candidate_id='unbound' if foreign_id else 'original',
            status='unavailable',reason='delivery_source_unavailable')])
    monkeypatch.setattr(delivery,'_call',resolve)
    if foreign_id:
        with pytest.raises(ValueError,match='escaped its exact original instance'):
            delivery.freeze_sources(root,receipts,candidate_id=identity)
    else:
        resolved=delivery.freeze_sources(root,receipts,candidate_id=identity)
        assert resolved['items']==[dict(candidate_id=identity,status='unavailable',reason='delivery_source_unavailable')]
    assert budget.closeout_state(root)['actions']==[]


@pytest.mark.process_e2e
def test_inherited_screen_reuses_only_its_frozen_late_recovery(tmp_path,composed_registry,monkeypatch):
    import copy
    from scidiscovery.research_loop import execution, experiments, managed_wait, search
    from test_composed_execution_results import case
    root,_,config,cli,call=case(tmp_path,dict(collect=False))
    blocked=[dict(mechanism=label,basis='blocked',finding='Original accepted backend call remains pending.')
        for label in ('P','Q')]
    # Retain a historical pre-wait unknown seal; no production execution bypass.
    with monkeypatch.context() as patch:
        patch.setattr(managed_wait,'before_seal',lambda *args:None)
        finish(root,2,'W',[1],mechanism_findings=blocked,preserve_native_intent=True)
    producer_seal=c.read(execution.origin(root,2)/'result-identity.json')
    original_observation=root/producer_seal['observations']['path']
    old_observation=original_observation.read_bytes()
    assert c.read(original_observation)['managed_calls'][0]['status']=='unknown'
    finish(root,3,'A',[1,2],action='continue_w',search_choice=dict(dispositions=[],
        next_focus=dict(mechanism_ref='h001.m1',action='screen')))
    experiments.load(config).complete(call)
    assert cli('reconcile','--call-id',call)['status']=='known_success'
    assert controller.execution_recover(root,2)['status']=='execution_recovered'
    worker=c.build_job(root,4,'W',[1,2,3])
    assert len(worker['history']['execution_recoveries'])==1
    finish(root,4,'W',[1,2,3],mechanism_findings=[
        dict(mechanism='P',basis='observed',finding='Assess the same recovered original call.'),blocked[1]])
    finish(root,5,'A',[1,4],action='continue_w',search_choice=dict(dispositions=[
        dict(mechanism_ref='h001.m1',status='screened',reason='Assess W4 using its frozen recovered observation.')],
        next_focus=dict(mechanism_ref='h001.m1',action='improve')))
    finish(root,6,'W',[1,4,5])
    finish(root,7,'A',[1,6],action='finish')
    assert search.project(root,6)['mechanisms']['h001.m1']['status']=='screened'
    assert original_observation.read_bytes()==old_observation
    assert len(experiments.Records(config).calls())==1
    for step in range(1,8):
        outcome=execution.origin(root,step)/'native-outcome.json'
        c.write(outcome,dict(c.read(outcome),native_started=False))
    with controller._writer(root):
        pass
    child=tmp_path/'child';limits=dict(max_tokens=1000,max_seconds=30,max_steps=1)
    preview=fork_service.preview(root,6,child,budget=limits)
    fork_service.create(root,6,child,budget=limits,expected_preview_id=preview['preview_id'],
        operation_id='late-recovery-screen-evidence')
    shutil.rmtree(root)
    inherited=search.project(child,6)
    assert inherited['mechanisms']['h001.m1']['status']=='screened'
    assert inherited['opportunity_counts']=={'h001.m1':1}
    job,_,seal,_=history._source(child,4)
    assert search._screen_refs(child,job,seal,inherited)=={'h001.m1'}
    producer=originals.node(child,2)
    assert originals.resolve_ref(producer,producer_seal['observations']).read_bytes()==old_observation
    # Even though the child cut knows the late recovery, a W without that exact
    # frozen input may not acquire the terminal evidence from a newer prefix.
    missing=copy.deepcopy(job);missing['history']['execution_recoveries']=[]
    with pytest.raises(history.ObserverIntegrityError,match='lacks its exact known observation'):
        search._screen_refs(child,missing,seal,inherited)


@pytest.mark.parametrize('value,valid',[
    ('ordinary:parent',True),('x'*80,True),('x'*81,False),
    ('界'*26+'ab',True),('界'*27,False),
    ('source:'+'a'*64+':run-10000',True),
    ('source:'+'A'*64+':run-10000',False),
    ('source:'+'a'*64+':'+'x'*81,False),
])
def test_parent_id_bounds_keep_raw_contract_and_exact_origin_shape(value,valid):
    assert candidate_refs.valid_parent_id(value) is valid


def test_delivery_repair_refusal_binds_old_origin_not_new_same_label(inherited_candidates,monkeypatch):
    from scidiscovery.research_loop import engineering
    root,producer,old_candidate=inherited_candidates
    producer_sha=c.digest(originals.source_record(producer,'result-identity.json'))
    c.write(root/'jobs/004.json',dict(step=4,role='W',engineering_repair=dict(candidate_id='original',
        source=dict(step=2,seal_sha256=producer_sha))))
    job=dict(step=5,role='A',purpose='closeout')
    c.write(root/'jobs/005.json',job)
    record=root/'native-evidence/005'
    c.write(record/'result-identity.json',{})
    current={}
    # Isolate this exact-source refusal inside binding. These synthetic decision
    # and preparation gates confer no execution or audit qualification.
    monkeypatch.setattr(c,'validate',lambda work: dict(current))
    monkeypatch.setattr(delivery,'_choice',lambda *args: current['candidate_recommendation'])
    monkeypatch.setattr(delivery,'selected_sources',lambda *args,**kwargs: dict(items=[
        dict(candidate_id=current['candidate_recommendation'],status='audit_pending',
            objects=dict(artifact=dict(sha256='a'*64)))]))
    monkeypatch.setattr(delivery,'_settings',lambda *args: dict(delivery_policy=dict(adapter=dict(target={}))))
    monkeypatch.setattr(budget,'closeout_state',lambda *args: dict(begin=dict(delivery_sources={}),actions=[]))
    monkeypatch.setattr(engineering,'steps',lambda *args: [4])
    for chosen in (old_candidate,'original'):
        current.update(candidate_recommendation=chosen,next_action='deliver')
        c.write(record/'science-output.json',current)
        if chosen==old_candidate:
            with pytest.raises(delivery.DeliveryUnresolved,match='delivery_engineering_original_unrepaired'):
                delivery.binding(root,job,current)
        else:
            bound=delivery.binding(root,job,current)
            assert bound['candidate_id']=='original'
            assert bound['decision']['job_sha256']==c.digest(root/'jobs/005.json')
    assert not (root/'host-delivery').exists()
