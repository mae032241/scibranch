"""Typed TCAD output views from synthetic retained originals; no native execution."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scidiscovery.operations.execution import LocalFileDescriptor
from scidiscovery.research_loop import experiments as e
from tcad_artifact.managed_execution import TCADManagedExecution
from tcad_artifact import managed_execution as managed


@pytest.fixture
def tcad_outputs(tmp_path,monkeypatch):
    records = tmp_path / 'records'
    original_directory = records / 'calls/synthetic-call/tcad-originals'
    original_directory.mkdir(parents=True)
    (original_directory.parent/'outputs').mkdir()
    monkeypatch.setattr(e,'registration',lambda executor:(managed.REGISTRATION,'tcad')
        if executor=='tcad_execution:tcad' else pytest.fail('Unexpected synthetic executor'))

    def descriptor(name, raw, media='application/json'):
        path = original_directory / name
        path.write_bytes(raw)
        return LocalFileDescriptor(name=name, local_path=str(path),
                                   size_bytes=len(raw), media_type=media)

    digest = 'a' * 64
    native = descriptor('generated_' + digest, b'voltage,current\n0,0\n1,2\n', 'text/csv')
    scientific_json = descriptor('scientific-data.json', e.encoded(dict(sha256='a scientifically named variable', values=[1, 2])))
    control = descriptor('native-package.json', e.encoded(dict(capability_sha256=digest)))
    log = descriptor('tcad_log', b'Solver reported convergence.\n', 'text/plain')
    rows = [dict(name=item.name, relative_path=relative, media_type=item.media_type,
        size_bytes=item.size_bytes, output_class=classification) for item, relative, classification in (
            (native, 'curve.csv', 'solver_native'), (scientific_json, 'data.json', 'parser_derived'),
            (control, 'native-package.json', 'transport_derived'))]
    manifest = descriptor('tcad_manifest', e.encoded(dict(started_at=None, completed_at='2026-10-18T00:00:00Z',
        terminal_state='failed', exit_code=97, solver_exit_code=0, outputs=rows,
        error='required output missing: /private/host/' + digest,
        collection_errors=['required output missing: /private/host/' + digest])))
    wrapper = TCADManagedExecution.__new__(TCADManagedExecution)
    wrapper.operation_context = None
    wrapper.config = dict(record_dir=str(records), execution_interface=dict(executor='tcad_execution:tcad'))
    descriptors = (native, scientific_json, control, log, manifest)
    observation = dict(call_id='synthetic-call',outputs=[dict(descriptor=item.model_dump(mode='json'), path=item.local_path,
        size_bytes=item.size_bytes) for item in descriptors])
    return wrapper, descriptors, observation


def tree_bytes(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes() if path.is_file() else None
            for path in directory.rglob('*')}


def original_state(descriptors):
    result={}
    for item in descriptors:
        path=Path(item.local_path);raw=path.read_bytes();info=path.stat()
        result[item.local_path]=(raw,info.st_mode,info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
    return result


def freeze_projection(config,observation,adapter):
    selected=e.scientific_outputs(config,observation,adapter=adapter)
    return dict(observation,scientific_outputs=[dict(descriptor=item.model_dump(mode='json'),
        **e.proof(item.local_path)) for item in selected])


def test_shared_scientific_selector_keeps_native_bytes_and_projects_manifest_facts(tcad_outputs):
    from pathlib import Path
    wrapper, originals, observation = tcad_outputs
    original_bytes = {item.name: Path(item.local_path).read_bytes() for item in originals}
    before = original_state(originals)
    selected = e.scientific_outputs(wrapper.config, observation, adapter=wrapper)
    names = {item.name for item in selected}
    assert names == {'tcad_output_001.csv', 'scientific-data.json', 'tcad_log', 'tcad_execution_facts.json'}
    for item in selected:
        raw = Path(item.local_path).read_bytes()
        if item.name == 'tcad_output_001.csv':
            assert raw == original_bytes[originals[0].name]
        elif item.name == 'scientific-data.json':
            assert raw == original_bytes[item.name]
        elif item.name == 'tcad_execution_facts.json':
            facts = json.loads(raw)
            assert facts['terminal_state'] == 'failed' and facts['exit_code'] == 97
            assert facts['solver_exit_code'] == 0 and facts['collection_error_count'] == 1
            assert facts['collection_error_codes'] == ['required_output_missing']
            assert facts['error_present'] is True and facts['error_code'] == 'required_output_missing'
            assert facts['withheld_control_output_count'] == 1
            assert facts['outputs'][0] == dict(name='tcad_output_001.csv', scientific_path='curve.csv', path_view='exact_relative_path')
            assert 'sha256' not in raw.decode() and 'private/host' not in raw.decode()
            assert 'a' * 64 not in raw.decode()
    for item in originals:
        assert Path(item.local_path).read_bytes() == original_bytes[item.name]
    assert e.scientific_outputs(wrapper.config, observation, adapter=wrapper) == selected
    assert original_state(originals) == before
    facts=next(item for item in selected if item.name=='tcad_execution_facts.json')
    assert Path(facts.local_path)==Path(wrapper.config['record_dir'])/'calls/synthetic-call/outputs/tcad_execution_facts.json'
    private=Path(wrapper.config['record_dir'])/'scientific-output-views/tcad/v3/synthetic-call/private-scientific-path-bindings.json'
    assert private.is_file() and not (Path(facts.local_path).parent/private.name).exists()


@pytest.mark.parametrize('changed', ['manifest', 'native_output'])
def test_scientific_selector_does_not_bypass_full_native_inventory_validation(tcad_outputs, changed):
    from pathlib import Path
    wrapper, originals, observation = tcad_outputs
    target = originals[-1] if changed == 'manifest' else originals[0]
    Path(target.local_path).write_bytes(Path(target.local_path).read_bytes() + b' ')
    with pytest.raises(e.IntegrityError):
        e.scientific_outputs(wrapper.config, observation, adapter=wrapper)


def test_registered_historical_projection_never_builds_backend_and_separates_manifest_views(tcad_outputs, monkeypatch):
    from pathlib import Path
    wrapper, originals, observation = tcad_outputs
    config = wrapper.config
    first_observation=freeze_projection(config,observation,wrapper)
    assert freeze_projection(config,observation,wrapper)==first_observation
    manifest = originals[-1]
    value = json.loads(Path(manifest.local_path).read_bytes())
    value.update(terminal_state='cancelled', exit_code=130, error='cancelled_by_request')
    raw = e.encoded(value)
    source = Path(manifest.local_path).with_name('another-retained-manifest')
    source.write_bytes(raw)
    replacement = manifest.model_copy(update=dict(local_path=str(source), size_bytes=len(raw)))
    outputs = (*originals[:-1], replacement)
    (Path(config['record_dir'])/'calls/second-call/outputs').mkdir(parents=True)
    second_observation = dict(call_id='second-call',outputs=[dict(descriptor=item.model_dump(mode='json'), path=item.local_path,
        size_bytes=item.size_bytes) for item in outputs])
    second_observation=freeze_projection(config,second_observation,wrapper)
    monkeypatch.setattr(e, 'registration', lambda *args:pytest.fail('Frozen reads must not discover an installed adapter'))
    monkeypatch.setattr(e, 'load', lambda config: pytest.fail('Historical projection must not build a runtime'))
    first = e.scientific_outputs(config, first_observation)
    second = e.scientific_outputs(config, second_observation)
    assert e.scientific_outputs(config,first_observation)==first
    with pytest.raises(e.UnsupportedExecution,match='Legacy scientific projection is not frozen'):
        e.scientific_outputs(config,observation)
    first_facts = next(item for item in first if item.name == 'tcad_execution_facts.json')
    second_facts = next(item for item in second if item.name == 'tcad_execution_facts.json')
    assert first_facts.local_path != second_facts.local_path
    assert json.loads(Path(first_facts.local_path).read_bytes())['terminal_state'] == 'failed'
    assert json.loads(Path(second_facts.local_path).read_bytes())['terminal_state'] == 'cancelled'


@pytest.mark.parametrize('identity_component', ['', 'b' * 64 + '/'])
def test_scientific_paths_distinguish_cases_with_same_output_filename(tcad_outputs, identity_component):
    from pathlib import Path
    wrapper, originals, _ = tcad_outputs
    relative_a, relative_b = f'caseA/{identity_component}iv.plt', f'caseB/{identity_component}iv.plt'
    native_a = originals[0].model_copy(update=dict(name='generated_'+'b'*64))
    path_b = Path(native_a.local_path).with_name('retained-caseB-output')
    raw_b = b'Synthetic distinct case B observations.\n'
    path_b.write_bytes(raw_b)
    native_b = LocalFileDescriptor(name='generated_'+'c'*64,
        local_path=str(path_b),  size_bytes=len(raw_b), media_type='text/plain')
    value = json.loads(Path(originals[-1].local_path).read_bytes())
    value['outputs'][0].update(name=native_a.name, relative_path=relative_a)
    value['outputs'].append(dict(name=native_b.name, relative_path=relative_b, media_type=native_b.media_type,
        size_bytes=native_b.size_bytes, output_class='solver_native'))
    raw = e.encoded(value)
    manifest_path = Path(originals[-1].local_path).with_name('retained-dual-case-manifest')
    manifest_path.write_bytes(raw)
    manifest = originals[-1].model_copy(update=dict(local_path=str(manifest_path),
        size_bytes=len(raw)))
    descriptors = (native_a, native_b, *originals[1:-1], manifest)
    observation = dict(call_id='synthetic-call',outputs=[dict(descriptor=item.model_dump(mode='json'), path=item.local_path,
        size_bytes=item.size_bytes) for item in descriptors])
    before = original_state(descriptors)
    selected = e.scientific_outputs(wrapper.config, observation, adapter=wrapper)
    facts_descriptor = next(item for item in selected if item.name == 'tcad_execution_facts.json')
    facts = json.loads(Path(facts_descriptor.local_path).read_bytes())
    rows = {row['name']: row for row in facts['outputs']}
    assert rows['tcad_output_001.plt']['scientific_path'].startswith('caseA/')
    assert rows['tcad_output_002.plt']['scientific_path'].startswith('caseB/')
    assert all(rows[name]['scientific_path'].endswith('/iv.plt') for name in ('tcad_output_001.plt', 'tcad_output_002.plt'))
    expected_view = 'display_projection' if identity_component else 'exact_relative_path'
    assert rows['tcad_output_001.plt']['path_view'] == rows['tcad_output_002.plt']['path_view'] == expected_view
    assert 'b' * 64 not in json.dumps(facts)
    private_path=Path(wrapper.config['record_dir'])/'scientific-output-views/tcad/v3/synthetic-call/private-scientific-path-bindings.json'
    private = json.loads(private_path.read_bytes())
    assert private['outputs'][0]['original_path'] == relative_a
    assert private['outputs'][1]['original_path'] == relative_b
    assert original_state(descriptors) == before
    assert not (Path(facts_descriptor.local_path).parent/private_path.name).exists()


def test_projection_uses_exact_managed_configuration_namespace(tcad_outputs, tmp_path):
    from pathlib import Path
    from scidiscovery.research_loop import contracts as c,local_experiments,shared_input_views
    wrapper, originals, observation = tcad_outputs
    root = tmp_path / 'managed-instance'
    job = root / 'jobs/002.json'
    job.parent.mkdir(parents=True)
    job.write_text(json.dumps(dict(step=2, worker_view_version=1)))
    task = c.record_ref(job)
    record = root / 'host-runtime/experiment-control/002/records'
    (record / 'calls/synthetic-call').mkdir(parents=True)
    (record / 'calls/retained-original').write_bytes(b'Immutable permit call closure.')
    before = tree_bytes(record / 'calls')
    config = dict(record_dir=str(record), scope=dict(step=2, job_ref=task),
        execution_interface=wrapper.config['execution_interface'])
    work=c.workspace(root,2);work.parent.mkdir(parents=True)
    public=shared_input_views.dynamic_root(root,work,'retained-experiments')
    outputs=local_experiments.artifact_directory(config,observation['call_id']);outputs.mkdir(parents=True)
    original_before=original_state(originals)
    selected = e.scientific_outputs(config, observation, adapter=wrapper)
    facts = next(item for item in selected if item.name == 'tcad_execution_facts.json')
    assert Path(facts.local_path)==public/'synthetic-call/outputs/tcad_execution_facts.json'
    bindings=record.parent/'scientific-output-views/tcad/v3/synthetic-call/private-scientific-path-bindings.json'
    assert bindings.is_file() and not (outputs/bindings.name).exists()
    assert original_state(originals)==original_before
    assert tree_bytes(record / 'calls') == before


@pytest.mark.parametrize('managed_shape', ['host-runtime/experiment-control/task/wrong-records', 'project/nodes/002/wrong/lineage'])
def test_malformed_managed_projection_cannot_fall_back_to_standalone(tcad_outputs, tmp_path, managed_shape):
    wrapper, _, observation = tcad_outputs
    directory = tmp_path / managed_shape
    directory.mkdir(parents=True)
    with pytest.raises(e.IntegrityError, match='canonical standalone host directory'):
        e.scientific_outputs(dict(record_dir=str(directory),execution_interface=wrapper.config['execution_interface']),
            observation, adapter=wrapper)
    assert not (directory.parent / 'scientific-output-views').exists()


@pytest.mark.parametrize('output_kind',['mutable_alias','frozen_alias','exclusive','external_alias'])
def test_retention_checks_owned_mutable_aliases_before_any_seal(tcad_outputs,tmp_path,monkeypatch,output_kind):
    from scidiscovery.research_loop import local_experiments, storage_policy
    wrapper,originals,_=tcad_outputs
    directory=Path(wrapper.config['record_dir'])/'calls/synthetic-call'
    for item in originals:Path(item.local_path).chmod(0o640)
    target=Path(originals[-1].local_path)
    if output_kind=='external_alias':
        external=tmp_path/'external-manifest';external.write_bytes(target.read_bytes());external.chmod(0o640)
        originals=(*originals[:-1],originals[-1].model_copy(update=dict(local_path=str(external))))
        target=external
    alias=tmp_path/'manifest-alias'
    if output_kind!='exclusive':os.link(target,alias)
    if output_kind=='frozen_alias':target.chmod(0o400)
    before={Path(item.local_path):Path(item.local_path).stat().st_mode for item in originals}
    published={}
    wrapper.config['scope']=dict(step=2)
    wrapper.records=SimpleNamespace(directory=lambda call:directory,external=lambda call:'external-run',
        accepted=lambda call:dict(prepared=dict(local_path='/prepared',size_bytes=1)),
        publish=lambda call,name,value:published.update({name:value}))
    monkeypatch.setattr(wrapper,'_stage',lambda call:tmp_path/'stage')
    monkeypatch.setattr(local_experiments,'artifact_directory',lambda *args:tmp_path/'node-outputs')
    context=storage_policy.Deadline(10)
    if output_kind=='mutable_alias':
        with pytest.raises(e.IntegrityError,match='hard-link aliases'):
            wrapper._retain('synthetic-call',originals,dict(state='failed'),context)
        assert all(path.stat().st_mode==mode for path,mode in before.items())
        assert alias.stat().st_mode==before[target]
        assert published=={} and not (directory/'tcad-terminal-binding.json').exists()
    else:
        wrapper._retain('synthetic-call',originals,dict(state='failed'),context)
        retained=published['collected.json']['outputs']
        assert [item['local_path'] for item in retained]==[item.local_path for item in originals]
        assert all(Path(item['local_path']).samefile(original.local_path) for item,original in zip(retained,originals))
        for path,mode in before.items():
            assert path.stat().st_mode&0o777==(mode&0o777 if output_kind=='external_alias' and path==target else 0o400)
