"""Current material lists are referenced once and resolved by their owner."""
import copy
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import (contracts as c, continuation, handoff_state, history,
    material_references as m, originals, shared_input_views)
from test_direct_input_bindings import direct_instance, stopped_seals
from test_history_handoff import finish


def producer(tmp_path, count=570):
    root = tmp_path / 'producer'
    owner = originals.NodeSource(dict(goal_id='producer',
        settings_ref=dict(path=str(root / 'settings.json'))), 2, str(root), root)
    rows = [dict(source=str(root / f'originals/material-{number:04d}.bin'),
        path=f'inputs/parents/002/artifacts/material-{number:04d}.bin', size_bytes=number + 1,
        file_stat=[1, number + 1, number + 1, 123]) for number in range(count)]
    return {2: dict(owner=owner, job=dict(role='W', step=2), seal=dict(materials=rows))}


def test_r8_many_files_keep_one_list_reference_and_existing_explicit_ids(tmp_path):
    sources = producer(tmp_path)
    rows = c._public_materials(sources[2]['seal']['materials'])
    refs = m.references(rows, sources=sources)
    assert len(refs) == 1
    assert refs[0]['list_ref'] == m.list_ref(sources[2])
    assert refs[0]['ids'] == [f'W002-I{number:03d}' for number in range(1, 571)]
    job = dict(step=4, role='H', material_refs=refs, direct_materials=rows)
    record = c.job_record(job)
    assert set(record) == {'step', 'role', 'material_refs'}
    assert len(json.dumps(record)) < len(json.dumps(job)) // 5
    assert m.materials(refs, sources=sources) == rows
    assert '"source":' not in json.dumps(record)


@pytest.mark.parametrize('change', ['id', 'origin', 'path', 'duplicate'])
def test_r8_reference_identity_and_membership_fail_closed(tmp_path, change):
    sources = producer(tmp_path, 2)
    refs = m.references(c._public_materials(sources[2]['seal']['materials']), sources=sources)
    refs = copy.deepcopy(refs)
    if change == 'id':
        refs[0]['ids'][0] = 'W002-I999'
    elif change == 'origin':
        refs[0]['list_ref']['origin_identity']['goal_id'] = 'another-original'
    elif change == 'path':
        refs[0]['list_ref']['path'] = str(tmp_path / 'another-seal.json')
    else:
        refs[0]['ids'].append(refs[0]['ids'][0])
    with pytest.raises(ValueError):
        m.materials(refs, sources=sources)


def test_r8_loaded_consumers_use_own_list_only_and_never_read_ancestry(tmp_path, monkeypatch):
    sources = producer(tmp_path, 3)
    rows = c._public_materials(sources[2]['seal']['materials'])
    refs = m.references([rows[1]], sources=sources)
    selected = m.bind_selected([dict(label='W002-I002', origin_step=2,
        origin_result_ref=dict(path='an exact saved result'), material=rows[1])], sources=sources)
    # This malformed older relay cannot affect a selection from the producer's
    # own list. A consumer has no loader or acceptance fallback.
    sources[2]['seal']['relayed_material_refs'] = [dict(unreachable='ancestor')]
    def forbidden(*args, **kwargs):
        pytest.fail('A material-reference consumer attempted filesystem IO')
    monkeypatch.setattr(c, 'read', forbidden)
    monkeypatch.setattr(Path, 'read_text', forbidden)
    monkeypatch.setattr(Path, 'stat', forbidden)
    record = c.job_record(dict(material_refs=refs, direct_materials=[rows[1]], selected_inputs=selected))
    assert 'material' not in record['selected_inputs'][0]
    assert m.materials(refs, sources=sources) == [rows[1]]
    assert m.selected_materials(record['selected_inputs'], sources=sources) == selected


def test_r8_selection_indexes_each_current_producer_once(tmp_path, monkeypatch):
    sources = producer(tmp_path)
    rows = c._public_materials(sources[2]['seal']['materials'])
    selected = [dict(label=f'choice {number}', origin_step=2, origin_result_ref={}, material=row)
        for number, row in enumerate(rows)]
    calls = []
    original = m._index
    def indexing(facts):
        calls.append(facts['owner'].step)
        return original(facts)
    monkeypatch.setattr(m, '_index', indexing)
    bound = m.bind_selected(selected, sources=sources)
    assert calls == [2]
    calls.clear()
    assert m.selected_materials(m.selected_record(bound), sources=sources) == bound
    assert calls == [2]








def test_r8_repair_task_gate_reuses_inventory_and_keeps_metadata_reads_raw(tmp_path, monkeypatch):
    root, _ = direct_instance(tmp_path)
    stopped_seals(monkeypatch)
    finish(root, 1, 'H', [], action='experiment')
    finish(root, 2, 'W', [1], files={'model.bin': 'an exact original'})
    job = c.build_job(root, 3, 'A', [1, 2])
    inventory = c.CurrentInventory(root, job, c.host_record(c.workspace(root, 3)), 0, [], {})
    read = c.read
    def reading(path):
        if path.name == 'result-identity.json' and path.parent.name == '002':
            pytest.fail('A task identity gate reopened scientific material facts')
        return read(path)
    monkeypatch.setattr(c, 'read', reading)
    assert continuation.validate_task(root, 3, inventory=inventory) is job
    assert continuation.validate_task(root, 3) == c.job_record(job)
    inventory.job = dict(job, instruction='changed frozen task')
    with pytest.raises(ValueError, match='another task'):
        continuation.validate_task(root, 3, inventory=inventory)


def test_r8_requester_and_producer_job_views_share_one_operation_sources(tmp_path, monkeypatch):
    root, _ = direct_instance(tmp_path)
    stopped_seals(monkeypatch)
    finish(root, 1, 'H', [], action='experiment', files={'input.csv': 'one original input'})
    finish(root, 2, 'W', [1], files={'result.bin': 'one derived original'})
    c.build_job(root, 3, 'W', [2])
    requester_record = c.read(root / 'jobs/003.json')
    reads = []
    read = c.read
    original_json = originals._json
    def reading(path):
        if path.name == 'result-identity.json':
            reads.append(path)
        return read(path)
    def reading_original(path):
        if path == root / 'jobs/002.json':
            reads.append(path)
        return original_json(path)
    monkeypatch.setattr(c, 'read', reading)
    monkeypatch.setattr(originals, '_json', reading_original)
    sources = {}
    requester = handoff_state.material_inputs(root, requester_record, sources=sources)
    producer_record = sources[2]['job']
    assert 'direct_materials' not in producer_record
    producer_job = handoff_state.material_inputs(root, producer_record, sources=sources)
    assert [row['path'] for row in requester['direct_materials']] == [
        'inputs/parents/002/artifacts/result.bin', 'inputs/parents/001/artifacts/input.csv']
    assert [row['path'] for row in producer_job['direct_materials']] == ['inputs/parents/001/artifacts/input.csv']
    assert reads.count(root / 'jobs/002.json') == 1
    assert sorted(path.parent.name for path in reads if path.name == 'result-identity.json') == ['001', '002']
