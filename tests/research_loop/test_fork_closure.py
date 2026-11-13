"""Owner snapshots preserve opaque bytes without widening scientific task cuts."""
import shutil

import pytest

from execution_fixtures import execution_registry, remote_registry

from scidiscovery.research_loop import contracts as c, fork_service, fork_sources, originals
from test_fork_source import completed_source
from test_fork_declared_controls import declared_control


def _write(root, path, raw):
    actual = root / path
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_bytes(raw)
    return dict(path=path, size_bytes=len(raw))


def _copy(root, child):
    plan = fork_sources.inspect_source(root, 2)
    assert plan['blocked_reasons'] == []
    fork_sources.copy_source(plan, child / originals.FORK_DIRECTORY)
    return plan, originals.selected_source(child)


def _copy_closure(root, closure, child):
    """Keep explicit fixture roots alongside the production owner snapshot."""
    plan = fork_sources.inspect_source(root, 2)
    assert plan['blocked_reasons'] == []
    entries = {fork_sources._key(row['origin_identity'], row['path'], row): row for row in plan['entries']}
    entries.update(closure.entries)
    plan['entries'] = list(entries.values())
    plan['source_paths'].update(closure.paths)
    plan['origins'].update(closure.origins)
    plan['native_records'].update(closure.records)
    fork_sources.copy_source(plan, child / originals.FORK_DIRECTORY)
    fork_sources.validate_manifest(child, full=True)
    return originals.selected_source(child)


def test_arbitrary_adapter_record_and_opaque_json_survive_parent_removal(tmp_path, declared_control):
    row = declared_control
    root = row['root']
    child = tmp_path / 'child'
    plan, source = _copy(root, child)
    path = row['opaque'].relative_to(root).as_posix()
    assert next(item for item in plan['entries'] if item['path'] == path)['visibility'] == 'host_proof'
    assert not any(item['path'].startswith('native-homes/') for item in plan['entries'])
    row['opaque'].unlink()
    assert originals.resolve_ref(source, dict(path=path)).read_bytes() == row['raw']
    fork_sources.validate_manifest(child, full=True)


def test_private_future_output_is_retained_but_not_in_scientific_index(tmp_path):
    root = completed_source(tmp_path)
    future = _write(root, 'project/nodes/999/future.json', b'future evidence')
    draft = _write(root, 'native-evidence/002/result.pending.json', b'{"summary":"unfinished"}\n')
    plan, source = _copy(root, tmp_path / 'child')
    paths = {row['path'] for row in source.entries}
    assert future['path'] not in paths
    assert 'native-evidence/002/science-output.json' not in paths
    assert 'native-evidence/001/science-output.json' in paths
    assert future['path'] not in {row['path'] for row in plan['entries']}
    assert draft['path'] not in paths
    assert next(row for row in plan['entries'] if row['path'] == draft['path'])['visibility'] == 'private_snapshot'
    assert originals.resolve_ref(source, draft).read_bytes() == (root / draft['path']).read_bytes()
    assert [row['step'] for row in plan['prefix_entries']] == [1]
    assert source.step == 2


@pytest.mark.parametrize('damage', ['missing', 'size_changed'])
def test_missing_or_changed_explicit_input_still_fails_closed(tmp_path, damage):
    root = completed_source(tmp_path)
    job = c.read_job(root / 'jobs/002.json')
    binding = job['global_materials'][0]
    selected = originals.resolve_ref(originals.node(root, 2), binding)
    logical = dict(path='project/nodes/002/' + binding['path'],
        size_bytes=binding['size_bytes'])
    _, inherited = _copy(root, tmp_path / 'child')
    assert originals.resolve_ref(inherited, logical) == selected
    assert selected.stat().st_size == binding['size_bytes']
    if damage == 'missing':
        selected.unlink()
    else:
        selected.chmod(0o600)
        with selected.open('ab') as stream:stream.write(b'\nchanged declared original\n')
        assert selected.stat().st_size != binding['size_bytes']
    # Fork inspection retains scientific locators without reopening bodies.
    # The actual selected-input consumer enforces presence and declared size;
    # arbitrary equal-size public edits are not a frozen content identity.
    with pytest.raises((OSError, ValueError)):
        originals.resolve_ref(inherited, logical)


def test_unknown_owner_is_explicit_gap_and_native_home_is_excluded(tmp_path):
    root = completed_source(tmp_path)
    _write(root, 'native-homes/001/secret', b'never retain native credentials')
    _write(root, 'undeclared-store/original', b'unknown owning namespace')
    plan = fork_sources.inspect_source(root, 2)
    assert plan['blocked_reasons'] == []
    assert not any(row['path'].startswith(('native-homes/', 'undeclared-store/')) for row in plan['entries'])
    fork_sources.copy_source(plan, tmp_path / 'child')
    # Unknown effects on a real accepted attempt still block, unlike an
    # arbitrary unaccepted directory that has no lifecycle authority.
    outcome = root / 'native-evidence/002/native-outcome.json'
    value = c.read(outcome)
    c.write(outcome, dict(value, native_started=True, returncode=None, cleanup='unknown'))
    reasons = fork_sources._parent_state(root)
    assert any(row['code'] == 'unknown_native_effect' for row in reasons)
    with pytest.raises(ValueError, match='unresolved original effects'):
        fork_sources.copy_source(dict(plan, blocked_reasons=reasons), tmp_path / 'blocked-child')


@pytest.mark.parametrize('locator', ['missing', 'reused'])
def test_explicit_old_locator_resolves_same_origin_archive_without_discovering_sources(tmp_path, locator):
    root = completed_source(tmp_path)
    raw = b'old prepared bytes'
    old = _write(root, 'host-recovery/custom/prepared.bin', raw)
    from scidiscovery.research_loop import history
    current = root / old['path']
    current.chmod(0o400)
    archive = _write(root, 'host-recovery/custom/stopped-original.bin', raw)
    (root / archive['path']).chmod(0o400)
    archived = history._managed_proof(root, root / archive['path'], old)
    receipt_path = 'host-recovery/custom/retained-original.json'
    c.write(root / receipt_path, dict(original=old, archive=archived))
    receipt = originals.original_ref(originals.node(root, 2), receipt_path)
    if locator == 'missing':
        current.unlink()
    else:
        current.chmod(0o600)
        current.write_bytes(b'new bytes at the old locator')
    child = tmp_path / 'child'
    closure = fork_sources._Closure()
    owner = originals.node(root, 2)
    closure.control_proof(owner, archived)
    closure.control_proof(owner, receipt)
    source = _copy_closure(root, closure, child)
    shutil.rmtree(root)
    # This explicitly retained host receipt tests offline identity resolution;
    # it claims neither a valid live managed call nor scientific qualification.
    frozen = c.read(originals.resolve_ref(source, receipt))
    assert frozen == dict(original=old, archive=archived)
    assert originals.resolve_ref(source, frozen['archive']).read_bytes() == raw
    assert old['path'] not in {row['path'] for row in source.entries}
    with pytest.raises(ValueError):
        originals.resolve_ref(source, frozen['original'])
    with pytest.raises(ValueError):
        originals.resolve_ref(source, dict(archived, size_bytes=archived['size_bytes'] + 1))
    with pytest.raises(ValueError):
        originals.resolve_ref(source, dict(path='host-recovery/custom/absent.bin'))
    retained = originals.resolve_ref(source, archived)
    retained.chmod(0o600)
    retained.write_bytes(b'tampered archived original')
    with pytest.raises(ValueError, match='size changed'):
        originals.resolve_ref(source, archived)


def test_archived_fallback_cannot_hide_tampering_of_exact_locator_version(tmp_path):
    root = completed_source(tmp_path)
    proof = _write(root, 'host-recovery/custom/prepared.bin', b'exact accepted bytes')
    archive = _write(root, 'host-history/objects/accepted-original', b'exact accepted bytes')
    (root / proof['path']).chmod(0o400)
    (root / archive['path']).chmod(0o400)
    closure = fork_sources._Closure()
    owner = originals.node(root, 2)
    closure.reference(owner, proof['path'], proof=proof)
    closure.reference(owner, archive['path'], proof=archive)
    source = _copy_closure(root, closure, tmp_path / 'child')
    retained = originals.resolve_ref(source, proof)
    retained.chmod(0o600)
    retained.write_bytes(b'damaged exact locator version')
    with pytest.raises(ValueError, match='size changed'):
        originals.resolve_ref(source, proof)


def test_declared_in_instance_host_key_is_excluded_and_undeclared_key_remains_gap(tmp_path):
    from scidiscovery.research_loop import execution_authority
    root = completed_source(tmp_path)
    key = root / 'private-operator-key'
    key.write_bytes(b'x' * 32)
    key.chmod(0o600)
    (root / 'execution-authority').mkdir(exist_ok=True)
    # Bind against a fixture host contract; no execution grant or signature
    # verification is inferred from copying these records.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(execution_authority, 'host_contract', lambda _: dict(version=1))
        execution_authority.bind_secret(root, b'x' * 32, source=key)
    plan = fork_sources.inspect_source(root, 2)
    assert plan['blocked_reasons'] == []
    assert not any(row['path'] in {'private-operator-key', 'execution-authority/host-key-source.json'}
        for row in plan['entries'])
    _write(root, 'undeclared-operator-key', b'y' * 32)
    changed = fork_sources.inspect_source(root, 2)
    assert changed['blocked_reasons'] == []
    assert not any(row['path'] == 'undeclared-operator-key' for row in changed['entries'])
