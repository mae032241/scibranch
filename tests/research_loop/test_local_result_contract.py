"""Optional local declarations preserve science without qualifying it."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from scidiscovery.research_loop import experiments as e, generic_results
from scidiscovery.research_loop.local_experiments import LocalExecution
from test_local_managed import execution_registry, local


def declaration(direction='max'):
    return dict(version=1, metrics=[dict(name='score',value=.5,evaluation_key='declared-observation-v1',direction=direction)],
        outputs=[dict(path='artifacts/model.bin',label='Retained evaluated object')],
        candidates=[dict(label='Author choice',metric='score',artifact='artifacts/model.bin')])


def observed(result):
    return dict(call_id='call-1',scope=dict(step=2),status='known_success',result_protocol=e.LOCAL_RESULT_PROTOCOL,
        completed=dict(retention='archived'),execution=dict(result=result),outputs=[dict(name='model.bin')])


def test_describe_exposes_optional_scientific_descriptions_without_direction_default():
    public = json.loads(next(row.content for row in LocalExecution.capabilities() if row.key == 'local_command'))
    result = public['optional_result']
    assert result['path'] == 'artifacts/result.json'
    schema = result['schema']
    assert schema['required'] == ['version','metrics','outputs']
    direction = schema['properties']['metrics']['items']['properties']['direction']
    assert 'enum' not in direction and 'default' not in direction
    for description in ('min','maximize','target is zero',None,dict(target=0)):
        jsonschema.validate(declaration(description),schema)
    value=declaration();value['candidates']=[{'artifact':'artifacts/model.bin'}]
    jsonschema.validate(value,schema)
    assert public['domain_metrics'] is False


@pytest.mark.parametrize('direction', ['min','max','maximize','target is zero',None,[],dict(target=0)])
def test_result_preserves_scientific_direction_value_and_raw(tmp_path, direction):
    value = declaration(direction)
    path = tmp_path/'result.json';path.write_text(json.dumps(value))
    original = path.read_bytes()
    result = LocalExecution.read_result(tmp_path, [dict(name='model.bin')])
    assert result['status'] == 'available'
    assert result['metrics'] == value['metrics']
    assert result['candidates'] == value['candidates']
    candidate=generic_results.candidates([observed(result)])[0]
    assert candidate['direction']==direction and candidate['metric']==.5
    assert path.read_bytes() == original


@pytest.mark.parametrize('field', ['value','artifact','metric'])
def test_invalid_single_declaration_preserves_other_observations_and_raw(tmp_path, field):
    value = copy.deepcopy(declaration())
    value['metrics'].append(dict(name='other',value=7))
    if field == 'value':value['metrics'][0]['value'] = 'unmeasured'
    elif field == 'artifact':value['candidates'][0]['artifact'] = 'artifacts/unretained.bin'
    else:value['candidates'][0]['metric'] = ['unresolved']
    path = tmp_path/'result.json';path.write_text(json.dumps(value))
    original = path.read_bytes()
    result = LocalExecution.read_result(tmp_path, [dict(name='model.bin')])
    assert result['status'] == 'available' and result['diagnostics']
    assert dict(name='other',value=7) in result['metrics']
    assert result['outputs']==value['outputs']
    candidates=generic_results.candidates([observed(result)])
    if field=='artifact':assert candidates==[]
    else:
        assert len(candidates)==1
        assert candidates[0]['metric'] is candidates[0]['direction'] is candidates[0]['evaluation_key'] is None
    assert path.read_bytes() == original


def test_artifact_candidate_needs_no_metric_and_never_chooses_first_measurement(tmp_path):
    value=declaration();value['candidates']=[{'artifact':'artifacts/model.bin'}]
    value['metrics'].append(dict(name='another',value=100,direction='min'))
    (tmp_path/'result.json').write_text(json.dumps(value))
    result=LocalExecution.read_result(tmp_path,[dict(name='model.bin')])
    candidate=generic_results.candidates([observed(result)])[0]
    assert candidate['label'] is None
    assert candidate['metric'] is candidate['direction'] is candidate['evaluation_key'] is None
    assert result['metrics']==value['metrics']


def test_candidate_members_keep_identity_and_public_reference_when_list_order_changes(tmp_path):
    from scidiscovery.research_loop import candidate_refs
    value=declaration()
    value['outputs'].append(dict(path='artifacts/alternative.bin'))
    value['candidates'].append(dict(artifact='artifacts/alternative.bin'))
    projected=[];references=[]
    for members in (value['candidates'],list(reversed(value['candidates']))):
        (tmp_path/'result.json').write_text(json.dumps(dict(value,candidates=members)))
        result=LocalExecution.read_result(tmp_path,[dict(name='model.bin'),dict(name='alternative.bin')])
        row=dict(observed(result),outputs=[dict(name='model.bin'),dict(name='alternative.bin')])
        rows=generic_results.candidates([row])
        assert len(rows)==2 and len({item['id'] for item in rows})==2
        projected.append({item['evidence'][1]['name']:item['id'] for item in rows})
        references.append(candidate_refs.references([dict(role='W',step=2,
            observation=dict(candidates=rows,managed_calls=[row]))]))
    assert projected[0]==projected[1] and references[0]==references[1]
    assert set(references[0].values())=={'W002-C001/artifacts/model.bin','W002-C001/artifacts/alternative.bin'}


@pytest.mark.parametrize('fault',['duplicate-member','duplicate-output','undeclared-output','invalid-list','invalid-member'])
def test_invalid_candidate_bindings_preserve_valid_members_and_measurements(tmp_path,fault):
    value=declaration()
    value['outputs'].append(dict(path='artifacts/neighbor.bin'))
    neighbor=dict(artifact='artifacts/neighbor.bin')
    if fault=='duplicate-member':value['candidates'].append(dict(value['candidates'][0]))
    elif fault=='duplicate-output':value['outputs'].append(dict(value['outputs'][0]))
    elif fault=='undeclared-output':value['candidates'][0]['artifact']='artifacts/unlisted.bin'
    elif fault=='invalid-member':value['candidates'][0]=None
    if fault=='invalid-list':value['candidates']={}
    else:value['candidates'].append(neighbor)
    (tmp_path/'result.json').write_text(json.dumps(value))
    result=LocalExecution.read_result(tmp_path,[dict(name='model.bin'),dict(name='neighbor.bin'),dict(name='unlisted.bin')])
    assert result['status']=='available' and result['metrics']==value['metrics'] and result['diagnostics']
    assert result['candidates']==([] if fault=='invalid-list' else [neighbor])


@pytest.mark.parametrize('declaration',[{},dict(candidates=[]),dict(candidate=dict(artifact='artifacts/model.bin'))])
def test_only_explicit_list_members_register_candidates(tmp_path,declaration):
    value=dict(version=1,metrics=[dict(name='observation',value=1)],outputs=[dict(path='artifacts/model.bin')],**declaration)
    (tmp_path/'result.json').write_text(json.dumps(value))
    result=LocalExecution.read_result(tmp_path,[dict(name='model.bin')])
    assert result['status']=='available' and result['outputs']==value['outputs']
    assert generic_results.candidates([observed(result)])==[]


def test_long_scientific_text_is_retained_with_bounded_public_preview(tmp_path):
    text='科学说明'*5000
    value=declaration(dict(target=text));metric=value['metrics'][0]
    metric.update(name=text,unit=text,evaluation_key=text)
    value['candidates'][0].update(label=text,metric=text)
    value['outputs'][0]['label']=text
    path=tmp_path/'result.json';path.write_text(json.dumps(value));original=path.read_bytes()
    result=LocalExecution.read_result(tmp_path,[dict(name='model.bin')])
    assert result['status']=='available' and result['metrics']==value['metrics']
    candidate=generic_results.candidates([observed(result)])[0]
    assert candidate['label']==text and candidate['evaluation_key']==text
    public=LocalExecution.public_result(dict(call_id='call-1',status='known_success',result=result))
    assert len(e.encoded(public['result']))<=16_000
    assert public['result']['candidates'][0]['details_ref']=='result.json'
    assert all(row['details_ref']=='result.json' for row in public['result']['metrics']+public['result']['outputs'])
    assert e._run_view(public)['result']==public['result']
    assert path.read_bytes()==original


def test_bad_metric_or_output_does_not_discard_valid_neighbors(tmp_path):
    value=declaration()
    value['metrics']=[None,dict(name='score',value=.5),dict(name='score',value=4),dict(name='bad',value=True),dict(name='valid',value=9)]
    value['outputs'].extend([dict(path='artifacts/missing',label='unretained'),None])
    (tmp_path/'result.json').write_text(json.dumps(value))
    result=LocalExecution.read_result(tmp_path,[dict(name='model.bin')])
    assert result['metrics']==[dict(name='valid',value=9)]
    assert result['outputs']==value['outputs'][:1]
    assert len(result['diagnostics'])==7
    assert len(generic_results.candidates([observed(result)]))==1


@pytest.mark.parametrize('section,key', [('metric','direction'),('metric','unit'),('metric','evaluation_key'),
    ('metric','extra'),('output','label'),('output','extra'),('candidate','label'),('candidate','extra')])
def test_nonfinite_nested_description_is_locally_omitted_with_original_reference(tmp_path,section,key):
    value=declaration();value['metrics'].append(dict(name='neighbor',value=7))
    target={'metric':value['metrics'][0],'output':value['outputs'][0],'candidate':value['candidates'][0]}[section]
    target[key]={'nested':[{'target':'EXPONENT_OUT_OF_RANGE'}]}
    content=json.dumps(value).replace('"EXPONENT_OUT_OF_RANGE"','1e999')
    path=tmp_path/'result.json';path.write_text(content);original=path.read_bytes()
    result=LocalExecution.read_result(tmp_path,[dict(name='model.bin')])
    assert result['status']=='available' and dict(name='neighbor',value=7) in result['metrics']
    projected={'metric':result['metrics'][0],'output':result['outputs'][0],'candidate':result['candidates'][0]}[section]
    assert key not in projected
    assert any(row['code']=='scientific_description_projection_unavailable' and row['details_ref']=='result.json'
        for row in result['diagnostics'])
    assert json.loads(e.encoded(result))==result
    candidate=generic_results.candidates([observed(result)])[0]
    assert candidate['metric']==.5
    assert len(e.encoded(LocalExecution.public_result(dict(status='known_success',result=result))))<16_000
    assert path.read_bytes()==original


def test_overflowing_metric_value_stays_unknown_without_discarding_candidate(tmp_path):
    content=json.dumps(declaration()).replace('"value": 0.5','"value": 1e999')
    (tmp_path/'result.json').write_text(content)
    result=LocalExecution.read_result(tmp_path,[dict(name='model.bin')])
    assert result['status']=='available' and result['metrics']==[]
    assert result['outputs'] and result['diagnostics']
    candidate=generic_results.candidates([observed(result)])[0]
    assert candidate['metric'] is candidate['direction'] is candidate['evaluation_key'] is None
    assert json.loads(e.encoded(result))==result


@pytest.mark.process_e2e
def test_nonfinite_descriptions_preserve_real_completion_publish_and_original(local):
    root,script,config,_=local
    value=declaration();value['metrics'].append(dict(name='neighbor',value=7))
    value['metrics'][0]['direction']={'target':'EXPONENT_OUT_OF_RANGE'}
    value['metrics'][0]['extra']={'nested':['EXPONENT_OUT_OF_RANGE']}
    value['outputs'][0]['label']={'target':'EXPONENT_OUT_OF_RANGE'}
    value['candidates'][0]['label']={'target':'EXPONENT_OUT_OF_RANGE'}
    value['candidates'][0]['extra']={'nested':['EXPONENT_OUT_OF_RANGE']}
    content=json.dumps(value).replace('"EXPONENT_OUT_OF_RANGE"','1e999')
    script.write_text('import pathlib\npathlib.Path("artifacts/model.bin").write_bytes(b"original")\n'
        'pathlib.Path("artifacts/result.json").write_text('+repr(content)+')\n')
    result=e.dispatch(config,script,command_options=dict(experiment_directory=str(root),arguments=[]))
    assert result['status']=='known_success' and result['result']['status']=='available'
    directory=e.Records(config).directory(result['call_id'])
    assert json.loads((directory/'completed.json').read_text())==result
    assert (directory/'outputs/result.json').read_text()==content
    assert e.load(config).result(result['call_id'])==result
    candidate=generic_results.candidates([observed(result['result'])])[0]
    assert candidate['metric']==.5 and candidate['direction'] is None and candidate['label'] is None
    assert dict(name='neighbor',value=7) in result['result']['metrics']
    assert e.progress(config)['calls'][0]['result']['status']=='available'
    assert len(e.Records(config).calls())==1


def test_unreadable_json_has_fixed_bounded_detail(tmp_path):
    (tmp_path/'result.json').write_text('{')
    result=LocalExecution.read_result(tmp_path,[])
    assert result['status']=='invalid' and result['diagnostic']=='result_json_invalid'
    assert str(tmp_path) not in result['detail']
    original=dict(result,detail='字'*512,private_control=dict(path='/private/control'))
    public=e._run_view(dict(result=original))['result']
    assert len(public['detail'].encode())<=512 and 'private_control' not in public


@pytest.mark.parametrize('output_kind',['mutable_alias','frozen_alias','exclusive'])
def test_code_version_completion_checks_all_mutable_aliases_before_sealing(tmp_path,monkeypatch,output_kind):
    from scidiscovery.research_loop import experiment_client, local_experiments
    directory=tmp_path/'call';directory.mkdir()
    artifacts=tmp_path/'artifacts';artifacts.mkdir()
    for name in ('accepted.json','process.json','stdout.raw','stderr.raw'):
        (directory/name).write_text('{}')
    (directory/'terminal.json').write_text(json.dumps(dict(wall_seconds=1)))
    script=tmp_path/'source.py';script.write_text('pass')
    first=artifacts/'a-first.bin';first.write_bytes(b'first');first.chmod(0o640)
    model=artifacts/'z-model.bin';model.write_bytes(b'model');model.chmod(0o640)
    value=declaration();value['outputs'][0]['path']='artifacts/z-model.bin'
    value['candidates'][0]['artifact']='artifacts/z-model.bin'
    (artifacts/'result.json').write_text(json.dumps(value));(artifacts/'result.json').chmod(0o640)
    alias=tmp_path/'model-alias'
    if output_kind!='exclusive':os.link(model,alias)
    if output_kind=='frozen_alias':model.chmod(0o400)
    before={path:path.stat().st_mode for path in artifacts.iterdir()}
    backend=LocalExecution.__new__(LocalExecution)
    backend.config=dict(max_seconds=10)
    backend.records=SimpleNamespace(root=tmp_path)
    monkeypatch.setattr(e,'_code_version_binding',lambda *args:tmp_path)
    monkeypatch.setattr(e,'_code_export_entry',lambda *args:None)
    monkeypatch.setattr(experiment_client,'root_for_configuration',lambda *args,**kwargs:None)
    monkeypatch.setattr(local_experiments,'artifact_directory',lambda *args:artifacts)
    accepted=dict(call_id='call',scope=dict(step=2),script=e.proof(script),code_version={},
        parent={},local_observation_version=1,experiment_directory=str(tmp_path))
    completed=backend.completion(directory,accepted,0,True,True)
    assert completed['status']=='known_success' and completed['returncode']==0
    if output_kind=='mutable_alias':
        assert completed['result']['status']=='unavailable'
        assert completed['result']['diagnostic']=='managed_output_invalid'
        assert completed['outputs']==[]
        assert generic_results.candidates([observed(completed['result'])])==[]
        assert all(path.stat().st_mode==mode for path,mode in before.items())
        assert alias.stat().st_mode==before[model]
    else:
        assert completed['result']['status']=='available'
        assert completed['result']['candidates']==value['candidates']
        assert all(path.stat().st_mode&0o777==0o400 for path in before)
        retained=next(row for row in completed['outputs'] if row['name']=='z-model.bin')
        assert Path(retained['path']).samefile(model) and retained['path']==str(model)
        if output_kind=='frozen_alias':assert alias.samefile(model)
    assert first.read_bytes()==b'first' and model.read_bytes()==b'model'


@pytest.mark.process_e2e
def test_actual_run_query_and_progress_preserve_freeform_direction(local, capsys):
    root,script,config,config_path = local
    value = declaration('maximize')
    script.write_text('import json,pathlib\n'
        'pathlib.Path("artifacts/model.bin").write_bytes(b"fixture object; no scientific qualification")\n'
        'pathlib.Path("artifacts/result.json").write_text('+repr(json.dumps(value))+')\n')
    prefix = ['--config',str(config_path)]
    assert e.main([*prefix,'run','--experiment-dir',str(root),'--script',str(script)]) == 0
    run = json.loads(capsys.readouterr().out)
    assert run['status'] == 'known_success'
    call = run['call_id']
    original = e.Records(config).directory(call)/'outputs/result.json'
    retained = original.read_bytes()
    assert e.main([*prefix,'query','--call-id',call]) == 0
    query = json.loads(capsys.readouterr().out)
    assert e.main([*prefix,'lineage-status']) == 0
    progress = json.loads(capsys.readouterr().out)
    for result in (run['result'],query['result'],progress['calls'][0]['result']):
        assert result['status']=='available' and result['metrics']==value['metrics']
        assert result['candidates']==value['candidates']
    assert original.read_bytes() == retained
    assert len(e.Records(config).calls()) == 1


@pytest.mark.process_e2e
@pytest.mark.parametrize('existing_output', ['retained', 'foreign'])
def test_terminal_only_local_reconciles_same_call_after_writer_stops(local, monkeypatch, existing_output):
    root, script, config, _ = local
    publish = e.Records.publish
    def lost_completion(self, call_id, filename, value):
        if filename != 'completed.json':return publish(self, call_id, filename, value)
    with monkeypatch.context() as patch:
        patch.setattr(e.Records, 'publish', lost_completion)
        e.dispatch(config, script, command_options=dict(experiment_directory=str(root), arguments=[]))
    records = e.Records(config)
    directory, = records.calls()
    call = directory.name
    assert (directory/'terminal.json').exists() and not (directory/'completed.json').exists()
    controls = {name:(directory/name).read_bytes() for name in ('accepted.json','process.json','terminal.json')}
    output = directory/'outputs/value.txt'
    if existing_output == 'foreign':
        output.unlink();output.write_bytes(b'99');output.chmod(0o400)
    original_output = e.proof(output)
    from scidiscovery.research_loop import shared_originals
    retain = shared_originals.retain
    mutable_copies = []
    def counted(*args, **kwargs):
        if kwargs.get('source_kind') == 'mutable':mutable_copies.append(args[1])
        return retain(*args, **kwargs)
    monkeypatch.setattr(shared_originals, 'retain', counted)
    adapter = e.load(config)
    with adapter._execution_writer_lease(call):
        assert adapter.result(call)['status'] == 'unknown'
        assert adapter.status(call) == 'unknown'
        assert not (directory/'completed.json').exists()
    result = adapter.result(call)
    assert result['status'] == 'known_success'
    assert result['result']['status'] == ('available' if existing_output == 'retained' else 'unavailable')
    assert e.proof(output) == original_output and mutable_copies == []
    assert adapter.status(call) == 'succeeded'
    assert adapter.collect_original(call)
    assert all((directory/name).read_bytes() == original for name, original in controls.items())
    assert len(records.calls()) == 1
    assert adapter.result(call) == result
