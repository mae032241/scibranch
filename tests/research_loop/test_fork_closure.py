"""Owner snapshots preserve opaque bytes without widening scientific task cuts."""
import hashlib
import json
import shutil

import pytest

from scidiscovery.research_loop import contracts as c, fork_service, fork_sources, originals
from test_fork_source import completed_source


def _write(root, path, raw):
    actual = root / path
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_bytes(raw)
    return dict(path=path, sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))


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
    plan['legacy_compilations'].update(closure.legacy_compilations)
    plan['native_records'].update(closure.records)
    plan['source_fingerprint'] = fork_sources.fingerprint(plan)
    fork_sources.copy_source(plan, child / originals.FORK_DIRECTORY)
    shutil.rmtree(root)
    fork_sources.validate_manifest(child, full=True)
    return originals.selected_source(child)


def test_arbitrary_adapter_record_and_opaque_json_survive_parent_removal(tmp_path):
    root = completed_source(tmp_path)
    outside = tmp_path / 'outside'
    outside.write_bytes(b'not an explicitly retained input')
    raw = json.dumps(dict(any_new_field=dict(path=str(outside), sha256=c.digest(outside)))).encode()
    proof = _write(root, 'host-recovery/non-domain-name/custom.pending', raw)
    child = tmp_path / 'child'
    plan, source = _copy(root, child)
    assert next(row for row in plan['entries'] if row['path'] == proof['path'])['visibility'] == 'private_snapshot'
    assert proof['path'] not in {row['path'] for row in source.entries}
    assert not any(row['sha256'] == c.digest(outside) for row in plan['entries'])
    shutil.rmtree(root)
    assert originals.resolve_ref(source, proof).read_bytes() == raw
    fork_sources.validate_manifest(child, full=True)


def test_private_future_output_is_retained_but_not_in_scientific_index(tmp_path):
    root = completed_source(tmp_path)
    future = _write(root, 'project/nodes/999/future.json', b'future evidence')
    plan, source = _copy(root, tmp_path / 'child')
    paths = {row['path'] for row in source.entries}
    assert future['path'] not in paths
    assert 'native-evidence/002/science-output.json' not in paths
    assert 'native-evidence/001/science-output.json' in paths
    assert next(row for row in plan['entries'] if row['path'] == future['path'])['visibility'] == 'private_snapshot'
    assert [row['step'] for row in plan['prefix_entries']] == [1]
    assert source.step == 2


def test_nested_snapshot_preserves_private_historical_versions_and_shared_inodes(tmp_path):
    root = completed_source(tmp_path)
    proof = _write(root, 'host-recovery/custom/original.bin', b'first historical bytes')
    child = tmp_path / 'child'
    budget = dict(max_tokens=200, max_seconds=30, max_steps=2)
    preview = fork_service.preview(root, 2, child, budget=budget)
    fork_service.create(root, 2, child, budget=budget,
        expected_preview_id=preview['preview_id'], operation_id='snapshot-child')
    inherited = originals.selected_source(child)
    original = originals.resolve_ref(inherited, proof)
    _write(child, proof['path'], b'new child bytes at the same logical path')
    closure = fork_sources._Closure()
    closure.snapshot(child)
    versions = [row for row in closure.entries.values() if row['path'] == proof['path']]
    assert len(versions) == 2
    historical = next(row for row in versions if row['sha256'] == proof['sha256'])
    retained = closure.paths[fork_sources._key(historical['origin_identity'], historical['path'], historical)]
    assert original.stat().st_ino == originals.files.check_path(retained).stat().st_ino
    assert originals.resolve_ref(inherited, proof).read_bytes() == b'first historical bytes'


def test_missing_or_changed_explicit_input_still_fails_closed(tmp_path):
    root = completed_source(tmp_path)
    selected = root / 'project/nodes/002/inputs/parents/001/conclusion.json'
    selected.chmod(0o600)
    selected.write_bytes(b'changed selected input')
    with pytest.raises((OSError, ValueError)):
        fork_sources.inspect_source(root, 2)


def test_unknown_owner_is_explicit_gap_and_native_home_is_excluded(tmp_path):
    root = completed_source(tmp_path)
    _write(root, 'native-homes/001/secret', b'never retain native credentials')
    _write(root, 'undeclared-store/original', b'unknown owning namespace')
    plan = fork_sources.inspect_source(root, 2)
    assert any(row.get('path') == 'undeclared-store' for row in plan['blocked_reasons'])
    assert not any(row['path'].startswith('native-homes/') for row in plan['entries'])
    with pytest.raises(ValueError, match='unresolved original effects'):
        fork_sources.copy_source(plan, tmp_path / 'child')


@pytest.mark.process_e2e
def test_forked_author_preserves_unavailable_review_constraint_after_parent_removal(tmp_path, monkeypatch):
    from scidiscovery.research_loop import controller, formal_review, instance_control
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    from test_formal_review import scripted
    parent, _ = scripted(tmp_path, monkeypatch, missing=True)
    assert controller.run(parent)['status'] == 'finish'
    original = formal_review.unavailable(parent, 3)
    child = tmp_path / 'forked-author'
    budget = dict(max_tokens=1000, max_seconds=60, max_steps=2)
    preview = fork_service.preview(parent, 4, child, budget=budget)
    assert preview['status'] == 'fork_preview_ready'
    fork_service.create(parent, 4, child, budget=budget,
        expected_preview_id=preview['preview_id'], operation_id='unavailable-review-child')
    inherited = originals.node(child, 3)
    path = 'native-evidence/003/formal-review-unavailable.json'
    assert path not in {row['path'] for row in inherited.entries}
    assert originals.has_original(inherited, path)
    shutil.rmtree(parent)
    assert formal_review.unavailable(child, 3) == original
    assert instance_control.continue_instance(child)['status'] == 'finish'
    job = c.read(child / 'jobs/004.json')
    assert len(job['formal_review_choices']['unavailable']) == 1
    assert formal_review.public_choices(child, job)['unavailable'] == [
        dict(subject='W002-I001', reason='subject_authorship_unsupported')]
    payload = c.validate(c.workspace(child, 4))
    with pytest.raises(OutputRepairRequired, match='exact subject and identity'):
        formal_review.validate_choice(child, job, dict(payload,
            next_action='formal_review', implementation_selection=['W002-I001']))
    # New verified author evidence changes the exact failure facts. This does
    # not qualify the subject and does not modify a retained native receipt.
    with monkeypatch.context() as changed_identity:
        changed_identity.setattr(formal_review, 'authors', lambda *args, **kwargs: dict(
            complete=True, participants=[['fixture', 'newly-verified-author']], reason=None))
        formal_review.validate_choice(child, job, dict(payload,
            next_action='formal_review', implementation_selection=['W002-I001']))


@pytest.mark.parametrize('locator', ['missing', 'reused'])
def test_explicit_old_locator_resolves_same_origin_archive_without_discovering_sources(tmp_path, locator):
    root = completed_source(tmp_path)
    raw = b'old prepared bytes'
    old = _write(root, 'host-recovery/custom/prepared.bin', raw)
    from scidiscovery.research_loop import history
    current = root / old['path']
    archived = history._managed_proof(root, current, old)
    receipt_path = 'host-recovery/custom/retained-original.json'
    c.write(root / receipt_path, dict(original=old, archive=archived))
    receipt = originals.original_ref(originals.node(root, 2), receipt_path)
    if locator == 'missing':
        current.unlink()
    else:
        current.write_bytes(b'new bytes at the old locator')
    child = tmp_path / 'child'
    _, source = _copy(root, child)
    shutil.rmtree(root)
    # This explicitly retained host receipt tests offline identity resolution;
    # it claims neither a valid live managed call nor scientific qualification.
    frozen = c.read(originals.resolve_ref(source, receipt))
    assert frozen == dict(original=old, archive=archived)
    assert originals.resolve_ref(source, frozen['original']).read_bytes() == raw
    assert old['path'] not in {row['path'] for row in source.entries}
    with pytest.raises(ValueError):
        originals.resolve_ref(source, dict(old, size_bytes=old['size_bytes'] + 1))
    with pytest.raises(ValueError):
        originals.resolve_ref(source, dict(path='host-recovery/custom/absent.bin'))
    retained = originals.resolve_ref(source, archived)
    retained.chmod(0o600)
    retained.write_bytes(b'tampered archived original')
    with pytest.raises(ValueError, match='bytes changed'):
        originals.resolve_ref(source, old)


def test_archived_fallback_cannot_hide_tampering_of_exact_locator_version(tmp_path):
    root = completed_source(tmp_path)
    proof = _write(root, 'host-recovery/custom/prepared.bin', b'exact accepted bytes')
    archive = _write(root, 'host-history/objects/' + proof['sha256'], b'exact accepted bytes')
    (root / proof['path']).chmod(0o400)
    (root / archive['path']).chmod(0o400)
    _, source = _copy(root, tmp_path / 'child')
    retained = originals.resolve_ref(source, proof)
    retained.chmod(0o600)
    retained.write_bytes(b'damaged exact locator version')
    with pytest.raises(ValueError, match='bytes changed'):
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
    assert any(row.get('path') == 'undeclared-operator-key' for row in changed['blocked_reasons'])
    assert not any(row['path'] == 'undeclared-operator-key' for row in changed['entries'])
