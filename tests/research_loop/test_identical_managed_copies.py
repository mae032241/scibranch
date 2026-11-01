"""Exact publication collisions reuse the retained managed original once."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c
from scidiscovery.research_loop.output_repair import OutputRepairRequired


@pytest.fixture
def scene(tmp_path, monkeypatch):
    root = tmp_path / 'instance'
    work = root / '002'
    record = root / 'native-evidence/002'
    work.mkdir(parents=True)
    record.mkdir(parents=True)
    original = work / 'work/retained-experiments/call-1/outputs/model.bin'
    original.parent.mkdir(parents=True)
    original.write_bytes(bytes(range(256)) * 257)
    original.chmod(0o400)
    job = dict(role='W', step=2, handoff_protocol=c.HANDOFF_PROTOCOL, direct_materials=[])
    outputs = {'call-1': [dict(name='model.bin', path=original.relative_to(root).as_posix(), retention='archived')]}
    inventory = c.CurrentInventory(root, job, record, 0,
        [dict(call_id='call-1', status='known_success')], outputs, policy={})
    monkeypatch.setattr(c, 'workspace', lambda *args: work)
    monkeypatch.setattr(c, 'host_record', lambda *args: record)
    return root, work, original, inventory


def copy_at(scene, owned_path, *, data=None, mode=0o644):
    _, work, original, _ = scene
    copied = work / owned_path
    copied.parent.mkdir(parents=True, exist_ok=True)
    copied.write_bytes(original.read_bytes() if data is None else data)
    copied.chmod(mode)
    return copied


def accept(scene):
    root, _, _, inventory = scene
    inventory.materials, inventory.mutable_materials = c._material_inventory(root, 2, inventory=inventory)
    return inventory.materials


@pytest.mark.parametrize('owned_path', [
    'artifacts/experiments/call-1/artifacts/model.bin',
    'experiments/call-1/artifacts/model.bin',
])
@pytest.mark.parametrize('mode', [0o644, 0o400])
def test_exact_identical_copy_uses_original_and_one_byte_allowance(scene, owned_path, mode):
    root, _, original, inventory = scene
    copied = copy_at(scene, owned_path, mode=mode)
    assert not copied.samefile(original)
    before = (original.stat().st_ino, original.stat().st_mode, original.read_bytes())
    size = original.stat().st_size
    inventory.policy = dict(max_output_item_bytes=size, max_output_total_bytes=size)

    rows = accept(scene)

    assert rows == [dict(source=str(original),
        path='inputs/parents/002/artifacts/experiments/call-1/artifacts/model.bin', size_bytes=size)]
    assert inventory.mutable_materials == []
    assert inventory.redundant_materials == [copied]
    assert copied.exists()  # Acceptance only observes; publication owns cleanup.
    assert c.collect_materials(root, 2, inventory=inventory) == rows
    assert not copied.exists()
    assert (original.stat().st_ino, original.stat().st_mode, original.read_bytes()) == before


def test_two_owned_layouts_reuse_one_managed_original(scene):
    first = copy_at(scene, 'artifacts/experiments/call-1/artifacts/model.bin')
    second = copy_at(scene, 'experiments/call-1/artifacts/model.bin')
    _, _, original, inventory = scene
    rows = accept(scene)
    assert [row['source'] for row in rows] == [str(original)]
    assert inventory.redundant_materials == sorted([first, second])
    assert inventory.mutable_materials == []


@pytest.mark.parametrize('links', [
    '[model](artifacts/experiments/call-1/artifacts/model.bin)',
    '[model](experiments/call-1/artifacts/model.bin)',
    '[model](artifacts/experiments/call-1/artifacts/model.bin) '
    '[canonical](experiments/call-1/artifacts/model.bin)',
])
def test_scientific_links_resolve_retained_original_after_copy_cleanup(scene, links):
    root, work, _, inventory = scene
    copied = copy_at(scene, 'artifacts/experiments/call-1/artifacts/model.bin')
    rows = accept(scene)
    c.collect_materials(root, 2, inventory=inventory)
    assert not copied.exists()
    unresolved = dict(items=[], omitted_count=0)
    entries = c.artifact_entry_points(work, dict(scientific_result=links), rows, unresolved=unresolved)
    assert entries == [dict(label='model', path=rows[0]['path'])]
    assert unresolved == dict(items=[], omitted_count=0)


@pytest.mark.parametrize('different_size', [False, True])
def test_distinct_owned_content_still_requires_layout_correction(scene, different_size):
    _, _, original, inventory = scene
    data = original.read_bytes()
    data = data + b'new' if different_size else data[:-1] + bytes([data[-1] ^ 1])
    copied = copy_at(scene, 'artifacts/experiments/call-1/artifacts/model.bin', data=data)
    before = (copied.stat().st_mode, copied.read_bytes())
    with pytest.raises(OutputRepairRequired) as error:
        accept(scene)
    assert error.value.code == 'deliverable_path_collision'
    assert inventory.redundant_materials == []
    assert (copied.stat().st_mode, copied.read_bytes()) == before


def test_identical_content_at_another_publication_path_remains_authored(scene):
    copied = copy_at(scene, 'artifacts/analysis/model.bin')
    _, work, original, inventory = scene
    rows = accept(scene)
    assert {row['source'] for row in rows} == {str(original), str(copied)}
    assert inventory.mutable_materials == [copied]
    assert inventory.redundant_materials == []
    unresolved = dict(items=[], omitted_count=0)
    science = dict(scientific_result='[authored](artifacts/analysis/model.bin) '
        '[extra](artifacts/artifacts/analysis/model.bin) '
        '[unmatched](artifacts/experiments/call-1/model.bin)')
    assert c.artifact_entry_points(work, science, rows, unresolved=unresolved) == [
        dict(label='authored', path='inputs/parents/002/artifacts/analysis/model.bin')]
    assert [row['label'] for row in unresolved['items']] == ['extra', 'unmatched']
    assert unresolved['omitted_count'] == 0


def test_two_distinct_managed_originals_are_ambiguous_even_with_identical_bytes(scene):
    root, _, original, inventory = scene
    copied = copy_at(scene, 'artifacts/experiments/call-1/artifacts/model.bin')
    other = original.with_name('second.bin')
    other.write_bytes(original.read_bytes())
    other.chmod(0o400)
    inventory.scientific_outputs['call-1'].append(dict(name='model.bin',
        path=other.relative_to(root).as_posix(), retention='archived'))
    with pytest.raises(ValueError, match='Different managed originals') as error:
        accept(scene)
    assert not isinstance(error.value, OutputRepairRequired)
    assert copied.exists() and inventory.redundant_materials == []


def test_later_total_validation_failure_preserves_copies(scene):
    _, work, original, inventory = scene
    copied = copy_at(scene, 'artifacts/experiments/call-1/artifacts/model.bin')
    report = work / 'artifacts/report.txt'
    report.write_bytes(b'x')
    inventory.policy = dict(max_output_total_bytes=original.stat().st_size)
    with pytest.raises(ValueError, match='total byte budget'):
        accept(scene)
    assert copied.exists() and inventory.redundant_materials == []
    assert report.stat().st_mode & 0o777 == 0o644


def test_copy_cleanup_failure_does_not_block_valid_original_publication(scene, monkeypatch):
    root, _, original, inventory = scene
    copied = copy_at(scene, 'artifacts/experiments/call-1/artifacts/model.bin')
    rows = accept(scene)
    unlink = Path.unlink
    def denied(path, *args, **kwargs):
        if path == copied:raise PermissionError('copy cleanup unavailable')
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', denied)
    assert c.collect_materials(root, 2, inventory=inventory) == rows
    assert copied.exists() and [row['source'] for row in rows] == [str(original)]
