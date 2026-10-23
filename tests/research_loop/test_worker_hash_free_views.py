"""New scientific projections preserve original validation and scientific bytes."""
import json
import os
import re
import sys
from pathlib import Path

import pytest

from scidiscovery.research_loop import author_closeout, candidate_refs, contracts as c, controller, execution, experiments, native
from execution_fixtures import execution_registry
from test_history_handoff import finish
from test_lineage_view import scene
from test_native_execution_inputs import chain, _reviewed_execution


def assert_no_control_identity(value):
    if isinstance(value,dict):
        assert not any(key in {'sha256','source_sha256','model_sha256','job_sha256',
            'execution_key','source_h_sha256','native_originals','native_originals_index',
            'retirement_proof','originals'} for key in value)
        for item in value.values():assert_no_control_identity(item)
    elif isinstance(value,list):
        for item in value:assert_no_control_identity(item)
    elif isinstance(value,str):
        assert re.search(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])',value) is None


def local_candidate(tmp_path):
    root=scene(tmp_path);finish(root,1,'H',[],action='experiment')
    c.build_job(root,2,'W',[1]);record=execution.origin(root,2)
    c.write(record/'native-intent.json',dict(task_sha256=c.digest(root/'jobs/002.json')))
    config=experiments.configure(root,2,'local_execution:command',dict(run_argv=[sys.executable]),
        c.workspace(root,2)/'work/experiment-config.json')
    directory=c.workspace(root,2)/'experiments/measurement';directory.mkdir(parents=True)
    script=directory/'run.py'
    script.write_text('import json,pathlib\n'
        'print("scientific sha256 digest "+"b"*64)\n'
        'print(pathlib.Path.cwd())\n'
        'print(__file__,file=__import__("sys").stderr)\n'
        'pathlib.Path("artifacts/observation.txt").write_text("scientific data with sha256 as a meaningful word")\n'
        'pathlib.Path("artifacts/result.json").write_text(json.dumps(dict(version=1,metrics=['
        'dict(name="sha256 science metric",value=3,direction="max",evaluation_key="measurement.v1")],'
        'outputs=[dict(path="artifacts/observation.txt",label="observation")],'
        'candidates=[dict(label="Measured candidate",metric="sha256 science metric",artifact="artifacts/observation.txt")])))\n')
    reply=experiments.dispatch(config,script,command_options=dict(experiment_directory=str(directory)))
    assert reply['status']=='known_success'
    return root,config,reply['call_id']


def test_new_scientific_task_keeps_machine_identities_private_and_original_science_exact(tmp_path):
    root=scene(tmp_path)
    science='The sha256 field and hashlib.sha256 algorithm are scientifically relevant.'
    source='import hashlib\nsha256 = hashlib.sha256(b"scientific input").hexdigest()\n'
    _,first,_=finish(root,1,'H',[],text=science,files={'algorithm.py':source})
    job=c.build_job(root,2,'W',[1]);work=c.workspace(root,2)
    assert job['worker_view_version']==1 and c.settings(root)['worker_view_version']==1
    assert_no_control_identity(c.read(work/'science-task.json'))
    assert (work/'inputs/parents/001/artifacts/algorithm.py').read_text()==source
    assert c.read(work/'inputs/parents/001/conclusion.json')['scientific_result']==science
    assert (first/'artifacts/algorithm.py').read_text()==source
    # The host still binds and rejects changed original inputs.
    bound=work/'inputs/parents/001/artifacts/algorithm.py'
    bound.chmod(0o600);bound.write_text(source+'# tampered\n')
    with pytest.raises(ValueError):c._validate_inputs(root,work,job)


def test_retained_legacy_job_does_not_acquire_new_view_identity(tmp_path):
    root=scene(tmp_path);job=c.build_job(root,1,'H',[])
    # A historical task is represented by its original absence of this marker.
    job.pop('worker_view_version');job.pop('direct_originals')
    job['history'].pop('candidate_references')
    path=root/'jobs/001.json';c.write(path,job)
    before=path.read_bytes()
    assert c.build_job(root,1,'H',[])==job
    assert path.read_bytes()==before and 'worker_view_version' not in c.read(path)


def test_retained_route_checks_exact_original_parent_and_validates_public_projection(tmp_path,monkeypatch):
    root=scene(tmp_path);write=c.write
    def compact_native(path,value):
        if path.name=='science-output.json':
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(value,separators=(',',':')))
        else:write(path,value)
    monkeypatch.setattr(c,'write',compact_native)
    finish(root,1,'H',[],action='experiment')
    job=c.build_job(root,2,'W',[1]);work=c.workspace(root,2)
    assert job['direct_refs'][0]['sha256']!=job['direct_originals'][0]['sha256']
    assert job['direct_originals'][0]['sha256']==c.digest(execution.raw(root,1))
    assert controller._check_job(root,c.settings(root),2,'W',[1],'',None)==job
    c._validate_inputs(root,work,job)
    with pytest.raises(ValueError,match='sealed scientific route'):
        controller._check_job(root,c.settings(root),2,'W',[],'',None)
    projection=work/job['direct_refs'][0]['path']
    projection.chmod(0o600);projection.write_text('{}')
    # Route identity is an original-parent check; input validation separately
    # rejects a corrupted public projection before any Worker can execute.
    assert controller._check_job(root,c.settings(root),2,'W',[1],'',None)==job
    with pytest.raises(ValueError):c._validate_inputs(root,work,job)


def test_normal_controller_loop_routes_independently_serialized_parent_views(tmp_path,monkeypatch):
    root=scene(tmp_path);write=c.write;launched=[]
    def compact_native(path,value):
        if path.name=='science-output.json':
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(value,separators=(',',':')))
        else:write(path,value)
    monkeypatch.setattr(c,'write',compact_native)
    def complete(root,step):
        job=c.read(root/'jobs'/f'{step:03d}.json');launched.append(job['role'])
        parents=[int(Path(ref['path']).parts[2]) for ref in job['direct_refs']]
        finish(root,step,job['role'],parents,action='experiment' if job['role']=='H' else 'finish')
    monkeypatch.setattr(native,'execute_native',complete)
    result=controller.run(root)
    assert result['status']=='finish' and result['completed_steps']==3
    assert launched==['H','W','A']
    for step in (2,3):
        job=c.read(root/'jobs'/f'{step:03d}.json')
        assert job['direct_refs'][0]['sha256']!=job['direct_originals'][0]['sha256']
        c._validate_inputs(root,c.workspace(root,step),job)


@pytest.mark.process_e2e
def test_author_science_facts_verify_real_calls_without_publishing_receipt_catalogue(tmp_path,execution_registry):
    root,config,call=local_candidate(tmp_path)
    directory=execution.origin(root,2)
    before={p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    private=author_closeout.terminal_facts(root,2)
    calls_before=author_closeout._calls(root,2)
    view=author_closeout.science_facts(root,2)
    assert author_closeout._calls(root,2)==calls_before
    assert private['calls'][0]['originals']
    assert view['calls'][0]['call_id']==call
    assert view['calls'][0]['status']=='known_success'
    assert_no_control_identity(view)
    assert not any('accepted.json' in json.dumps(item) or 'completed.json' in json.dumps(item)
        for item in view['calls'])
    assert {item['name'] for item in view['calls'][0]['outputs']}=={'observation.txt','result.json'}
    logs=view['calls'][0]['logs_ref'];assert {item['stream'] for item in logs['streams']}=={'stdout','stderr'}
    readable={item['stream']:(c.workspace(root,2)/item['path']).read_text() for item in logs['streams']}
    assert 'b'*64 in readable['stdout']  # Meaningful scientific hashes are original science, not control identities.
    assert str(c.workspace(root,2)/'experiments/measurement') in readable['stdout']
    assert str(c.workspace(root,2)/'experiments/measurement/run.py') in readable['stderr']
    assert not any(config['record_dir'] in text or 'host-history/objects' in text for text in readable.values())
    observed=c.read(Path(config['record_dir'])/'calls'/call/'execution-observation.json')
    collection=experiments._collected_view(config,call,observed,experiments.load(config))
    assert {item['name'] for item in collection['outputs']}=={'observation.txt','result.json',
        'execution-logs/stdout.txt','execution-logs/stderr.txt'}
    assert all(Path(item['path']).is_file() for item in collection['outputs'])
    assert view['omitted_details'] and all(set(item)=={'category','scope','reason'} for item in view['omitted_details'])
    assert before=={p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    # Real original completion mutation is rejected before a public view exists.
    from scidiscovery.research_loop.experiment_client import record_directory
    completed=record_directory(root,2)/'calls'/call/'completed.json'
    completed.chmod(0o600);completed.write_text('{}')
    with pytest.raises(ValueError):author_closeout.science_facts(root,2)


@pytest.mark.process_e2e
def test_real_collect_streams_exact_logs_reuses_cas_and_recovers_same_call(tmp_path,execution_registry,monkeypatch,capsys):
    from scidiscovery.research_loop import atomic_files, local_experiments, scientific_log_projection as logs
    root,config,call=local_candidate(tmp_path)
    directory=Path(config['record_dir'])/'calls'/call
    observation=c.read(directory/'execution-observation.json')
    accepted=c.read(directory/'accepted.json')
    evidence=[directory/name for name in ('accepted.json','completed.json','execution-observation.json',
                                         'stdout.raw','stderr.raw','terminal.json')]
    before={path:path.read_bytes() for path in evidence}
    replacements={str(directory):f'[retained execution {call}]',
        config['record_dir']:'[retained executions]',accepted['script']['path']:accepted['prepared']['local_path'],
        str(directory/'inputs'):accepted['experiment_directory']}
    for item in accepted['snapshot_files']:
        replacements[str(directory/'inputs'/item['name'])]=item['path']
    replacements[str(directory/'outputs')]=str(Path(accepted['experiment_directory'])/'artifacts')
    expected={}
    for stream in ('stdout','stderr'):
        raw=before[directory/(stream+'.raw')]
        for source,destination in sorted(replacements.items(),key=lambda row:len(row[0]),reverse=True):
            raw=raw.replace(os.fsencode(source),os.fsencode(destination))
        expected[stream]=raw
    raw_views={Path(item['path']) for item in observation['outputs']
        if item['source']['path'] in {str(directory/'stdout.raw'),str(directory/'stderr.raw')}}
    read_bytes=Path.read_bytes
    def bounded_only(path):
        if path in raw_views:pytest.fail('Real collect read an entire retained raw log')
        return read_bytes(path)
    monkeypatch.setattr(Path,'read_bytes',bounded_only)
    monkeypatch.setattr(local_experiments.subprocess,'Popen',lambda *a,**k:pytest.fail('Collect repeated execution'))
    project=logs.project;publish=atomic_files.publish_stream
    contexts=[];writes=[];failure=dict(enabled=False)
    def projected(path,**options):
        contexts.append(options['context'])
        options['block_size']=7
        def blocks():
            for index,block in enumerate(project(path,**options)):
                if failure['enabled'] and index==1:raise TimeoutError('original collect interrupted')
                yield block
        return blocks()
    def published(path,blocks,**options):
        def counted():
            for block in blocks:
                assert len(block)<=7
                writes.append(len(block))
                yield block
        return publish(path,counted(),**options)
    monkeypatch.setattr(logs,'project',projected)
    monkeypatch.setattr(atomic_files,'publish_stream',published)
    config_path=c.workspace(root,2)/'work/experiment-config.json'
    args=['--config',str(config_path),'collect','--call-id',call]
    capsys.readouterr()
    def collect():
        contexts.clear()
        code=experiments.main(args)
        reply=json.loads(capsys.readouterr().out)
        assert contexts and all(context is contexts[0] for context in contexts)
        return code,reply
    code,first=collect()
    assert code==0 and first['status']=='collected' and first['call_id']==call
    assert sum(writes)==sum(map(len,expected.values()))
    for stream in ('stdout','stderr'):
        target=next(Path(item['path']) for item in first['outputs']
            if item['name']=='execution-logs/'+stream+'.txt')
        assert read_bytes(target)==expected[stream]
        assert config['record_dir'].encode() not in expected[stream]
        assert b'host-history/objects' not in expected[stream]
    assert b'b'*64 in expected['stdout']
    first_writes=list(writes)
    code,second=collect()
    assert code==0 and second['outputs']==first['outputs'] and writes==first_writes
    failure['enabled']=True
    code,failed=collect()
    assert code==3 and failed['status']=='integrity_error'
    failure['enabled']=False
    code,recovered=collect()
    assert code==0 and recovered['outputs']==first['outputs'] and writes==first_writes
    assert experiments.Records(config).calls()==[directory]
    assert before=={path:read_bytes(path) for path in evidence}


@pytest.mark.process_e2e
def test_real_candidate_reference_roundtrip_keeps_private_canonical_and_public_parent_projection(tmp_path,execution_registry):
    root,config,call=local_candidate(tmp_path)
    assert not (c.workspace(root,2)/'work/lineage/calls').exists()
    assert not Path(config['record_dir']).is_relative_to(c.workspace(root,2))
    citation=f'experiments/{call}/artifacts/observation.txt'
    finish(root,2,'W',[1],text=f'[Observed scientific measurement]({citation})')
    seal=c.read(execution.origin(root,2)/'result-identity.json')
    public_path=f'inputs/parents/002/artifacts/{citation}'
    assert seal['entry_points']==[dict(label='Observed scientific measurement',path=public_path)]
    retained=next(item for item in seal['materials'] if item['path']==public_path)
    assert Path(retained['source']).parent==root/'host-history/objects'
    assert_no_control_identity(seal['entry_points'])
    invalid=dict(retained,source=str(root/'not-a-retained-scientific-output'))
    with pytest.raises(ValueError):c.artifact_entry_points(c.workspace(root,2),dict(scientific_result=''),[invalid])
    author=c.build_job(root,3,'A',[2]);mapping=candidate_refs.for_job(author)
    assert len(mapping)==1
    canonical,reference=next(iter(mapping.items()))
    assert canonical.startswith('local-') and re.fullmatch(r'W002-C\d{3}/artifacts/observation\.txt',reference)
    page=c.read(c.workspace(root,3)/author['history']['view']['indexes']['candidates'])
    assert page['candidates'][0]['id']==reference
    assert_no_control_identity(c.read(c.workspace(root,3)/'science-task.json'))
    assert canonical not in json.dumps(page)
    # Arbitrary science and meaningful metric names remain untouched.
    assert 'sha256 science metric' in json.dumps(author['history']['view']) or any(
        'sha256 science metric' in path.read_text() for path in (c.workspace(root,3)/'inputs/history').rglob('*.json'))
    finish(root,3,'A',[2],recommendation=reference)
    raw=execution.raw(root,3);original=raw.read_bytes()
    assert c.read(raw)['candidate_recommendation']==reference
    assert c.validate(c.workspace(root,3))['candidate_recommendation']==canonical
    assert raw.read_bytes()==original
    successor=c.build_job(root,4,'H',[3])
    public=c.read(c.workspace(root,4)/successor['direct_refs'][0]['path'])
    assert public['candidate_recommendation']==reference
    assert successor['direct_originals'][0]['sha256']==c.digest(raw)
    assert canonical not in json.dumps(public)
    with pytest.raises(ValueError,match='outside this frozen'):
        candidate_refs.resolve_reference(root,author,'W002-C999')
    assert candidate_refs.control_payload(author,dict(candidate_recommendation=canonical))['candidate_recommendation']==canonical
    assert c.read(c.workspace(root,4)/'science-task.json')['parents'][0]['conclusion']==successor['direct_refs'][0]['path']
    raw.chmod(0o600);raw.write_bytes(original+b' ')
    with pytest.raises(ValueError):c._validate_inputs(root,c.workspace(root,4),successor)
    asset=Path(retained['source']);asset.chmod(0o600);asset.write_bytes(asset.read_bytes()+b' changed')
    with pytest.raises(ValueError,match='exact retained output binding'):
        c.artifact_entry_points(c.workspace(root,2),dict(scientific_result=f'[Observed]({citation})'),[retained])


@pytest.mark.process_e2e
def test_author_history_and_wake_publish_selected_science_outputs_only(tmp_path,execution_registry,monkeypatch):
    from scidiscovery.research_loop import history,managed_wait
    root,config,call=local_candidate(tmp_path);select=experiments.scientific_outputs
    def selected(config,observation,**kwargs):
        return tuple(item for item in select(config,observation,**kwargs) if item.name=='observation.txt')
    monkeypatch.setattr(experiments,'scientific_outputs',selected)
    facts=author_closeout.science_facts(root,2)
    assert [item['name'] for item in facts['calls'][0]['outputs']]==['observation.txt']
    job=c.read(root/'jobs/002.json')
    observation=Path(config['record_dir'])/'calls'/call/'execution-observation.json'
    managed_wait.wake_packet(root,job,dict(execution_wake=dict(observations=[experiments.proof(observation)])))
    packet=c.read(c.workspace(root,2)/'work/execution-results.json')
    assert [item['name'] for item in packet['executions'][0]['outputs']]==['observation.txt']
    finish(root,2,'W',[1]);seal=c.read(execution.origin(root,2)/'result-identity.json')
    original=c.read(root/seal['observations']['path'])['managed_calls'][0]
    assert {item['name'] for item in original['outputs']}=={'observation.txt','result.json'}
    assert [item['name'] for item in history.scientific_outputs(root,job,original)]==['observation.txt']
    author=c.build_job(root,3,'A',[2]);page=c.read(c.workspace(root,3)/author['history']['view']['indexes']['experiments'])
    assert len(page['experiments'][0]['outputs'])==1
    assert page['experiments'][0]['outputs'][0]['path'].endswith('/observation.txt')


@pytest.mark.process_e2e
def test_legacy_author_science_context_projects_bound_candidates_without_rewriting_originals(tmp_path,execution_registry,monkeypatch):
    root,_,_=local_candidate(tmp_path);finish(root,2,'W',[1])
    # Construct retained pre-view framework pages from exact current sources.
    with monkeypatch.context() as patch:
        patch.setattr(candidate_refs,'references',lambda records:{})
        job=c.build_job(root,3,'W',[2])
    job.pop('worker_view_version');job.pop('direct_originals');job['history'].pop('candidate_references')
    c.write(root/'jobs/003.json',job)
    work=c.workspace(root,3)
    originals={path:path.read_bytes() for path in [root/'jobs/003.json',work/'science-task.json',*sorted((work/'inputs').rglob('*'))] if path.is_file()}
    assert 'local-' in (work/'science-task.json').read_text()
    record=execution.origin(root,3)/'continuations/001';record.mkdir(parents=True)
    proof=author_closeout.science_context(root,job,record)
    directory=author_closeout.validate_science_context(root,job,proof)
    assert_no_control_identity(c.read(directory/'science-task.json'))
    archived=[item for item in job['direct_materials'] if Path(item['source']).parent==root/'host-history/objects']
    assert archived
    assert all((directory/item['path']).read_bytes()==Path(item['source']).read_bytes() for item in archived)
    assert originals=={path:path.read_bytes() for path in originals}
    view=directory/'science-task.json';view.chmod(0o600);c.write(view,dict(c.read(view),role='A'))
    with pytest.raises(ValueError):author_closeout.validate_science_context(root,job,proof)


def test_public_replay_diagnostic_keeps_private_originals_out_of_science(tmp_path):
    from scidiscovery.research_loop import replay_check
    directory=tmp_path/'call';directory.mkdir()
    c.write(directory/'accepted.json',{});c.write(directory/'completed.json',{})
    c.write(directory/replay_check.INTENT,dict(accepted_sha256=c.digest(directory/'accepted.json'),
        completed_sha256=c.digest(directory/'completed.json')))
    c.write(directory/replay_check.OUTCOME,dict(intent_sha256=c.digest(directory/replay_check.INTENT),result=dict(
        status='dependency_error',note='dependency_unavailable_in_declared_sample_scope',stage='source_import',
        cleanup_confirmed=True,diagnostic='/private/host-runtime/'+'a'*64+'/source.py: missing dependency')))
    original=(directory/replay_check.OUTCOME).read_bytes()
    accepted=dict(engineering_check=dict(capability=dict(policy=dict(check_scope={})),deadline_boottime=1))
    public=replay_check._preview(directory,accepted,scientific=True)
    assert public['diagnostic']['present'] is True and public['status']=='dependency_error'
    assert '/private/' not in json.dumps(public) and 'a'*64 not in json.dumps(public)
    assert (directory/replay_check.OUTCOME).read_bytes()==original


@pytest.mark.parametrize('damage',['author','package','client'])
def test_compiled_subject_review_reads_science_and_preserves_exact_private_binding(chain,capsys,damage):
    from scidiscovery.research_loop import execution_inputs,formal_review,qualification
    root,_,output,_,_,_=_reviewed_execution(chain,capsys)
    job=c.read(root/'jobs/004.json');public=formal_review.public(root,job)
    reviewed=c.workspace(root,4)/public['review']['scientific_implementation']
    assert reviewed.read_bytes()==chain[5].read_bytes()
    assert [c.workspace(root,4)/path for path in public['review']['scientific_inputs']]
    assert (c.workspace(root,4)/public['review']['scientific_inputs'][0]).read_bytes()==b'mesh'
    seal=c.read(execution.origin(root,2)/'result-identity.json');entry=seal['compiled_implementations'][0]
    material=entry['material'];package=execution_inputs.compiled_payload(root,material)
    assert package['path']!=material['source']
    compiler=qualification._read(root,entry['compiler'])
    target={'author':root/compiler['author']['path'],'package':Path(package['path']),'client':output}[damage]
    target.chmod(0o600);target.write_bytes(target.read_bytes()+b' ')
    with pytest.raises(ValueError):execution_inputs.compiled_payload(root,material)
