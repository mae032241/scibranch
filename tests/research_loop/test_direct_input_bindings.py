"""Small real producers and consumers for direct originals; no native processes."""
from pathlib import Path
import subprocess

import pytest

from scidiscovery.operations.execution import LocalFileDescriptor
from scidiscovery.research_loop import (accepted_budget, atomic_files, budget, contracts as c, controller,
    experiment_client, experiments as e, originals, qualification as q,
    shared_input_views, shared_originals)
from scidiscovery.research_loop.protocol import stage_scope
from test_direct_public_collect import replace_equal_bytes, stopped_attempt
from test_history_handoff import finish


def direct_instance(tmp_path, *, updates=(), delivery_policy=None):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Compare exact bounded scientific originals.')
    objective.chmod(0o640)
    root=tmp_path/'instance'
    options={}
    if delivery_policy is not None:
        options.update(delivery_policy=delivery_policy,
            node_budget_policy={role:dict(max_observed_tokens=1000,max_seconds=30) for role in ('H','W','A','E')})
    controller.prepare(root,objective,public,['fake'],max_tokens=10000,max_seconds=300,
        max_steps=8,model='fixture',reasoning_effort='high',search_policy=None,
        user_updates=list(updates),experiment_mode='external',
        source_restrictions=[dict(source=str(path),restriction='clear') for path in (objective,*updates)],
        **options)
    return root,objective


def stopped_seals(monkeypatch):
    original=c.seal_result
    def seal(root,step):
        job=c.read(root/'jobs'/f'{step:03d}.json')
        stopped_attempt(root,job,c.host_record(c.workspace(root,step)),monkeypatch)
        return original(root,step)
    monkeypatch.setattr(c,'seal_result',seal)
    def forbidden(*args,**kwargs):
        pytest.fail('Direct-original fixture reached a process boundary')
    for name in ('Popen','run','call'):monkeypatch.setattr(subprocess,name,forbidden)


def test_bound_inputs_reference_originals_without_task_body_copies(tmp_path,monkeypatch):
    dataset=tmp_path/'dataset.csv';dataset.write_bytes(b'x,y\n'+b'1,2\n'*16383)
    dataset.chmod(0o640)
    saved=e.proof(dataset);mode=dataset.stat().st_mode;body=dataset.read_bytes()
    copies=[];writes=[];chmods=[]
    copy=atomic_files.copy_original;publish=atomic_files.publish_bytes;chmod=Path.chmod
    def copying(source,target,**options):
        if Path(source)==dataset:copies.append(Path(target))
        return copy(source,target,**options)
    def publishing(target,payload,**options):
        if payload==body:writes.append(Path(target))
        return publish(target,payload,**options)
    def changing_mode(path,value,*args,**options):
        if path==dataset:chmods.append(value)
        return chmod(path,value,*args,**options)
    monkeypatch.setattr(atomic_files,'copy_original',copying)
    monkeypatch.setattr(atomic_files,'publish_bytes',publishing)
    monkeypatch.setattr(Path,'chmod',changing_mode)
    root,objective=direct_instance(tmp_path,updates=(dataset,))
    stopped_seals(monkeypatch)
    finish(root,1,'H',[],action='experiment')
    second=c.build_job(root,2,'W',[1])
    assert second['global_materials']==c.read(root/'jobs/001.json')['global_materials']
    for step in (1,2):
        work=c.workspace(root,step);job=c.read(root/'jobs'/f'{step:03d}.json')
        row=next(row for row in job['global_materials'] if row['source']==str(dataset))
        assert set(row)=={'source','path','size_bytes'}
        assert not (work/row['path']).exists()
        assert originals.resolve_root_ref(root,dict(row,path=str(work/row['path'])))==dataset
        argv=shared_input_views.mount_arguments(root,job,work,include_skills=False)
        assert any(argv[index:index+3]==['--ro-bind',str(dataset),str(work/row['path'])]
            for index in range(len(argv)-2))
    assert not (root/'source-provenance/originals').exists()
    assert not (root/'project/global/objective.md').exists()
    assert e.proof(dataset)==saved and dataset.read_bytes()==body and dataset.stat().st_mode==mode
    assert copies==writes==chmods==[]
    assert objective.stat().st_mode & 0o777==0o640


def test_generated_inline_objective_is_published_once(tmp_path,monkeypatch):
    public=tmp_path/'public';public.mkdir();root=tmp_path/'instance'
    payload=b'One generated original objective.';writes=[];publish=atomic_files.publish_bytes
    def publishing(path,data,**options):
        if data==payload:writes.append(Path(path))
        return publish(path,data,**options)
    monkeypatch.setattr(atomic_files,'publish_bytes',publishing)
    controller.prepare(root,public/'unused.md',public,['fake'],max_tokens=1000,max_seconds=60,
        max_steps=2,model='fixture',reasoning_effort='high',search_policy=None,
        objective_text=payload.decode(),experiment_mode='external')
    row=c.build_job(root,1,'H',[])['global_materials'][0]
    original=root/'project/global/objective.md'
    assert originals.resolve_root_ref(root,dict(row,path=row['source']))==original
    assert original.read_bytes()==payload and writes==[original]
    assert not (c.workspace(root,1)/row['path']).exists()


def test_selected_material_rejects_same_size_replacement_from_private_seal(tmp_path,monkeypatch):
    root,_=direct_instance(tmp_path);stopped_seals(monkeypatch)
    _,work,record=finish(root,1,'H',[],action='experiment',files={'answer.txt':'one exact answer'})
    seal=c.read(record/'result-identity.json');private=seal['materials'][0]
    saved=dict(private);public=c._public_materials([private])[0]
    assert private['file_stat']==e.proof(work/'artifacts/answer.txt')['file_stat']
    assert set(public)=={'source','path','size_bytes'}
    successor=c.build_job(root,2,'W',[1])
    assert successor['direct_materials']==[public]
    mounts=shared_input_views.mount_arguments(root,successor,c.workspace(root,2),include_skills=False)
    assert any(mounts[index:index+3]==['--ro-bind',public['source'],str(c.workspace(root,2)/public['path'])]
        for index in range(len(mounts)-2))
    provenance=c.read(root/seal['source_provenance']['path'])
    assert provenance['materials']==[public]
    assert set(c.file_ref(Path(public['source'])))=={'path','size_bytes'}
    q._check(e.proof(public['source']))
    replace_equal_bytes(public['source'])
    # Projection consumes an accepted descriptor; actual material admission owns
    # the current original-file identity check.
    assert c._public_materials([private])==[public]
    for consume in (lambda:originals.material_path(root,public),
            lambda:shared_input_views.mount_arguments(root,successor,c.workspace(root,2),include_skills=False),
            lambda:q.output_status(root,1,public),
            lambda:q._check(dict(path=public['source'],size_bytes=public['size_bytes'],file_stat=private['file_stat']))):
        with pytest.raises(ValueError,match='changed|identity|original'):consume()
    assert c.read(record/'result-identity.json')['materials']==[saved]
    legacy=dict(path=public['source'],size_bytes=public['size_bytes'])
    assert originals.resolve_root_ref(root,legacy)==Path(public['source'])
    assert set(legacy)=={'path','size_bytes'}  # Legacy2 does not gain a current fingerprint.


def test_bound_compiled_package_without_code_version_uses_actual_original(tmp_path,monkeypatch):
    root,_=direct_instance(tmp_path);stopped_seals(monkeypatch)
    job=c.build_job(root,1,'W',[]);work=c.workspace(root,1)
    compiled=[]
    class Compiler:
        def capabilities(self):
            return (e.AdapterCapability(key='implementation_authoring',kind='implementation_authoring',
                schema_id='fixture.implementation.v1',payload_schema_version=1,media_type='application/json',
                content=b'{}',public_summary={}),)
        def compile_implementation(self,source,aliases,output):
            compiled.append(output)
            c.write(output,dict(source='a bounded generated package',inputs=aliases));output.chmod(0o400)
            return dict(status='compiled',path=str(output),qualification='unreviewed')
    compiler=Compiler()
    config=dict(record_dir=str(experiment_client.record_directory(root,1)),scope=stage_scope(root,job),
        max_seconds=60,worker_view_version=1,admission=dict(version=1,mode='hard'))
    interface=dict(executor='fixture:package',public_capabilities=e._public_capabilities(compiler))
    config['execution_interface']=interface
    source=work/'work/author.json';c.write(source,{'bounded':'author input'})
    client=work/'artifacts/package.json'
    record=c.host_record(work)
    intent=dict(task_ref=c.record_ref(root/'jobs/001.json'))
    anchor=budget.accept_node(root,job)
    if anchor is not None:intent['budget_slice']=anchor
    c.write(record/'native-intent.json',intent)
    accepted_budget.accept_native(root,1,record,intent)
    with pytest.raises(e.IntegrityError,match='exact accepted native attempt'):
        e.compile_implementation(config,compiler,source,{},client)
    assert compiled==[] and not client.exists()
    config['native_attempt']=dict(number=0,execution_key=intent['task_ref'])
    e.compile_implementation(config,compiler,source,{},client)
    assert len(compiled)==1
    package=experiment_client.payload_path(config,client);saved=e.proof(package)
    descriptor=LocalFileDescriptor(name='0001-package',local_path=str(package),
        size_bytes=saved['size_bytes'],media_type='application/json')
    accepted=e.Records(config).accept(descriptor,dict(parent=dict(candidate_id=None),
        source_script=str(client),snapshot_files=[]),interface,expected_calls=[],_local_preparation=True)
    assert 'code_version' not in accepted and accepted['source_script']==str(client)
    assert accepted['script']==saved and not (e.Records(config).directory(descriptor.name)/'source.py').exists()
    assert e.verify_scientific_inputs(accepted,config=config)==accepted
    replace_equal_bytes(package)
    with pytest.raises(ValueError,match='changed|original'):e.verify_scientific_inputs(accepted,config=config)


def test_provenance_status_uses_controls_without_reopening_ancestor_science(tmp_path,monkeypatch):
    root,_=direct_instance(tmp_path);stopped_seals(monkeypatch)
    _,first,first_record=finish(root,1,'H',[],action='experiment',files={'old-model.bin':'an old used input'})
    _,_,second_record=finish(root,2,'W',[1],files={'derivative.txt':'a sealed derivative'})
    before=q.output_status(root,2)
    assert before['restriction']=='clear'
    old=first/'artifacts/old-model.bin';old.unlink()
    (first_record/'science-output.json').unlink()
    assert q.output_status(root,2)==before
    private=c.read(first_record/'result-identity.json')['materials'][0]
    with pytest.raises((OSError,ValueError),match='original|file|No such|changed'):
        originals.material_path(root,private)
    # The accepted W output carries its inherited restriction. A query of that
    # exact output does not reopen its ancestor's proof or scientific bytes.
    proof=c.read(first_record/'result-identity.json')['source_provenance']
    (root/proof['path']).unlink()
    assert q.output_status(root,2)==before
    current_job=c.read_job(root/'jobs/002.json')
    current_seal=c.read(second_record/'result-identity.json')
    q.validate_outputs(root,current_job,current_seal)
    # The current output's own exact proof remains required at consumption.
    current_proof=current_seal['source_provenance']
    (root/current_proof['path']).unlink()
    with pytest.raises((OSError,ValueError),match='original|receipt|file|No such|identity'):
        q.output_status(root,2)
    with pytest.raises((OSError,ValueError),match='original|receipt|file|No such|identity'):
        q.validate_outputs(root,current_job,current_seal)
    assert (second_record/'result-identity.json').is_file()
