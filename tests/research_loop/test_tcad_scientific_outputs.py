"""Typed TCAD output views from synthetic retained originals; no native execution."""
import json

import pytest

from scidiscovery.operations.execution import LocalFileDescriptor
from scidiscovery.research_loop import experiments as e
from tcad_artifact.managed_execution import TCADManagedExecution
from tcad_artifact import managed_execution as managed


@pytest.fixture
def tcad_outputs(tmp_path):
    records = tmp_path / 'records'
    original_directory = records / 'calls/synthetic-call/tcad-originals'
    original_directory.mkdir(parents=True)

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
    observation = dict(outputs=[dict(descriptor=item.model_dump(mode='json'), path=item.local_path,
        size_bytes=item.size_bytes) for item in descriptors])
    return wrapper, descriptors, observation


def tree_bytes(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes() if path.is_file() else None
            for path in directory.rglob('*')}


def test_shared_scientific_selector_keeps_native_bytes_and_projects_manifest_facts(tcad_outputs):
    from pathlib import Path
    wrapper, originals, observation = tcad_outputs
    original_bytes = {item.name: Path(item.local_path).read_bytes() for item in originals}
    calls = Path(wrapper.config['record_dir']) / 'calls'
    original_tree = tree_bytes(calls)
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
    assert tree_bytes(calls) == original_tree


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
    monkeypatch.setattr(e, 'registration', lambda executor: (managed.REGISTRATION, 'tcad'))
    monkeypatch.setattr(e, 'load', lambda config: pytest.fail('Historical projection must not build a runtime'))
    config = wrapper.config
    first = e.scientific_outputs(config, observation)
    manifest = originals[-1]
    value = json.loads(Path(manifest.local_path).read_bytes())
    value.update(terminal_state='cancelled', exit_code=130, error='cancelled_by_request')
    raw = e.encoded(value)
    source = Path(manifest.local_path).with_name('another-retained-manifest')
    source.write_bytes(raw)
    replacement = manifest.model_copy(update=dict(local_path=str(source), size_bytes=len(raw)))
    outputs = (*originals[:-1], replacement)
    second_observation = dict(outputs=[dict(descriptor=item.model_dump(mode='json'), path=item.local_path,
        size_bytes=item.size_bytes) for item in outputs])
    second = e.scientific_outputs(config, second_observation)
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
    native_a = originals[0].model_copy(update=dict(name='generated_caseA'))
    path_b = Path(native_a.local_path).with_name('retained-caseB-output')
    raw_b = b'Synthetic distinct case B observations.\n'
    path_b.write_bytes(raw_b)
    native_b = LocalFileDescriptor(name='generated_caseB',
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
    observation = dict(outputs=[dict(descriptor=item.model_dump(mode='json'), path=item.local_path,
        size_bytes=item.size_bytes) for item in descriptors])
    calls = Path(wrapper.config['record_dir']) / 'calls'
    original_tree = tree_bytes(calls)
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
    private = json.loads((Path(facts_descriptor.local_path).parent / 'private-scientific-path-bindings.json').read_bytes())
    assert private['outputs'][0]['original_path'] == relative_a
    assert private['outputs'][1]['original_path'] == relative_b
    assert tree_bytes(calls) == original_tree


def test_projection_uses_exact_managed_configuration_namespace(tcad_outputs, tmp_path):
    from pathlib import Path
    from scidiscovery.research_loop import contracts as c
    wrapper, _, observation = tcad_outputs
    root = tmp_path / 'managed-instance'
    job = root / 'jobs/002.json'
    job.parent.mkdir(parents=True)
    job.write_text(json.dumps(dict(step=2, worker_view_version=1)))
    task = c.record_ref(job)
    record = root / 'host-runtime/experiment-control/002/records'
    (record / 'calls').mkdir(parents=True)
    (record / 'calls/retained-original').write_bytes(b'Immutable permit call closure.')
    before = tree_bytes(record / 'calls')
    config = dict(record_dir=str(record), scope=dict(step=2, job_ref=task))
    selected = e.scientific_outputs(config, observation, adapter=wrapper)
    facts = next(item for item in selected if item.name == 'tcad_execution_facts.json')
    assert Path(facts.local_path).is_relative_to(record.parent / 'scientific-output-views')
    assert tree_bytes(record / 'calls') == before


@pytest.mark.parametrize('managed_shape', ['host-runtime/experiment-control/task/wrong-records', 'project/nodes/002/wrong/lineage'])
def test_malformed_managed_projection_cannot_fall_back_to_standalone(tcad_outputs, tmp_path, managed_shape):
    wrapper, _, observation = tcad_outputs
    directory = tmp_path / managed_shape
    directory.mkdir(parents=True)
    with pytest.raises(e.IntegrityError, match='canonical standalone host directory'):
        e.scientific_outputs(dict(record_dir=str(directory)), observation, adapter=wrapper)
    assert not (directory.parent / 'scientific-output-views').exists()
