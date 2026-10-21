"""Exact mutable locators and opaque compiler bytes in portable source cuts."""
import hashlib
import json
import shutil

import pytest

from scidiscovery.research_loop import contracts as c, fork_sources, originals
from test_fork_source import completed_source


def _proof(path, raw):
    return dict(path=path, sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))


def _write(root, path, raw):
    actual = root / path
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_bytes(raw)
    return _proof(path, raw)


def _accepted(root, call, prepared):
    path = f'host-runtime/experiment-control/fixture/calls/{call}/accepted.json'
    c.write(root / path, dict(call_id=call, prepared=dict(
        local_path=str(root / prepared['path']), sha256=prepared['sha256'], size_bytes=prepared['size_bytes'])))
    return path


def _copy_closure(root, closure, child):
    # Start with the real selected task cut, then add the independently collected
    # typed fixture records. Publication and all inherited reads use production
    # code, including its source rechecks and manifest validation.
    plan = fork_sources.inspect_source(root, 2)
    assert plan['blocked_reasons'] == []
    plan['entries'].extend(closure.entries.values())
    plan['source_paths'].update(closure.paths)
    plan['origins'].update(closure.origins)
    plan['source_fingerprint'] = fork_sources.fingerprint(plan)
    fork_sources.copy_source(plan, child / originals.FORK_DIRECTORY)
    shutil.rmtree(root)
    fork_sources.validate_manifest(child, full=True)
    return originals.selected_source(child)


def test_current_prepared_original_needs_no_archive_and_survives_parent_removal(tmp_path):
    root = completed_source(tmp_path)
    proof = _write(root, 'project/nodes/001/work/script.py', b'print("original")\n')
    closure = fork_sources._Closure()
    closure.add(originals.node(root, 2), _accepted(root, 'one', proof), parse=True)
    assert not any(row['path'].startswith('host-history/objects/') for row in closure.entries.values())
    child = tmp_path / 'child'
    source = _copy_closure(root, closure, child)
    assert originals.resolve_ref(source, proof).read_bytes() == b'print("original")\n'


def test_two_prepared_calls_keep_both_archived_versions_of_one_locator(tmp_path):
    root = completed_source(tmp_path)
    locator = 'project/nodes/001/work/script.py'
    variants = [b'first accepted script\n', b'second accepted script\n']
    proofs = [_proof(locator, raw) for raw in variants]
    for proof, raw in zip(proofs, variants):
        _write(root, 'host-history/objects/' + proof['sha256'], raw)
    _write(root, locator, b'the current file is neither accepted original\n')
    closure = fork_sources._Closure()
    for call, proof in zip(('one', 'two'), proofs):
        closure.add(originals.node(root, 2), _accepted(root, call, proof), parse=True)
    versions = [row for row in closure.entries.values() if row['path'] == locator]
    assert len(versions) == 2
    assert len({row['storage_path'] for row in versions}) == 2
    assert {'host-history/objects/' + proof['sha256'] for proof in proofs} <= {
        row['path'] for row in closure.entries.values()}
    child = tmp_path / 'child'
    source = _copy_closure(root, closure, child)
    for proof, raw in zip(proofs, variants):
        assert originals.resolve_ref(source, proof).read_bytes() == raw
    nested = fork_sources._Closure()
    for call in ('one', 'two'):
        nested.add(source, f'host-runtime/experiment-control/fixture/calls/{call}/accepted.json', parse=True)
    assert {row['sha256'] for row in nested.entries.values() if row['path'] == locator} == {
        proof['sha256'] for proof in proofs}
    with pytest.raises(ValueError, match='unique exact'):
        originals.resolve_ref(source, dict(path=locator))
    with pytest.raises(ValueError, match='unique exact'):
        originals.resolve_ref(source, dict(proofs[0], size_bytes=proofs[0]['size_bytes'] + 1))


@pytest.mark.parametrize('archive_bytes', [None, b'changed archive bytes'])
def test_prepared_original_missing_exact_current_and_retained_bytes_is_rejected(tmp_path, archive_bytes):
    root = completed_source(tmp_path)
    proof = _proof('project/nodes/001/work/script.py', b'missing accepted original\n')
    if archive_bytes is not None:
        _write(root, 'host-history/objects/' + proof['sha256'], archive_bytes)
    closure = fork_sources._Closure()
    with pytest.raises((OSError, ValueError)):
        closure.add(originals.node(root, 2), _accepted(root, 'one', proof), parse=True)
    assert not any(row['path'] == proof['path'] for row in closure.entries.values())


@pytest.mark.parametrize('author_is_json', [False, True])
def test_compiler_authored_capability_and_package_bytes_are_opaque(tmp_path, author_is_json):
    root = completed_source(tmp_path)
    future = _write(root, 'project/nodes/999/future.json', b'{"future":"outside the cut"}')
    scientific = json.dumps(dict(inputs=[future], files=[future])).encode()
    base = 'native-evidence/001/compiled-implementations/fixture'
    author_bytes = scientific if author_is_json else b'this authored document is not JSON\n'
    author = _write(root, base + '/author.json', author_bytes)
    capability = _write(root, base + '/capability.json', scientific)
    package = _write(root, base + '/package.json', scientific)
    compiler = base + '/compiler.json'
    c.write(root / compiler, dict(author=author, capability=capability, output=package))
    closure = fork_sources._Closure()
    closure.add(originals.node(root, 2), compiler, parse=True)
    assert {author['path'], capability['path'], package['path']} <= {row['path'] for row in closure.entries.values()}
    assert future['path'] not in {row['path'] for row in closure.entries.values()}
    child = tmp_path / 'child'
    source = _copy_closure(root, closure, child)
    assert originals.resolve_ref(source, author).read_bytes() == author_bytes
    assert originals.resolve_ref(source, capability).read_bytes() == scientific
    assert originals.resolve_ref(source, package).read_bytes() == scientific
    with pytest.raises(ValueError, match='outside the frozen fork closure'):
        originals.resolve_ref(source, future)


def test_json_suffix_in_native_evidence_does_not_declare_host_control_type(tmp_path):
    root = completed_source(tmp_path)
    future = _write(root, 'project/nodes/999/future.json', b'future scientific result')
    scientific = _write(root, 'native-evidence/001/scientific-document.json',
        json.dumps(dict(inputs=[future])).encode())
    closure = fork_sources._Closure()
    closure.refs(originals.node(root, 2), dict(evidence=[scientific]))
    assert [row['path'] for row in closure.entries.values()] == [scientific['path']]


def test_accepted_snapshot_inventory_keeps_call_member_after_authoring_path_changes(tmp_path):
    root = completed_source(tmp_path)
    path = 'host-runtime/experiment-control/fixture/calls/one/accepted.json'
    snapshot = 'host-runtime/experiment-control/fixture/calls/one/inputs/nested/value.json'
    original = 'project/nodes/001/work/value.json'
    proof = _write(root, snapshot, b'original call input\n')
    _write(root, original, b'later authoring input\n')
    c.write(root / path, dict(cwd=str((root / snapshot).parents[1]), snapshot_files=[
        dict(proof, name='nested/value.json', path=str(root / original))]))
    closure = fork_sources._Closure()
    closure.add(originals.node(root, 2), path, parse=True)
    paths = {row['path'] for row in closure.entries.values()}
    assert snapshot in paths
    assert original not in paths
    source = _copy_closure(root, closure, tmp_path / 'child')
    assert originals.resolve_ref(source, proof).read_bytes() == b'original call input\n'


def test_engineering_repair_keeps_exact_failure_and_objects_without_parsing_scientific_names(tmp_path):
    root = completed_source(tmp_path)
    failure_bytes = b'{"status":"engineering_error","cleanup_confirmed":true}'
    failure = _write(root, 'candidate-audit/fixture/outcome.json', failure_bytes)
    objects = {name: _write(root, f'host-delivery/objects/{index}', raw) for index, (name, raw) in enumerate([
        ('source.py', b'accepted source\n'), ('artifact', b'exact artifact\n'), ('extra-label', b'extra original\n')])}
    outside = _write(root, 'project/nodes/999/future.json', b'future scientific evidence')
    source = originals.node(root, 2)
    closure = fork_sources._Closure()
    # The actual repair failure record binds sha256 without size_bytes.
    closure.refs(source, dict(failure={key: failure[key] for key in ('path', 'sha256')}, objects=objects),
        context='engineering_repair')
    closure.refs(source, dict(failure=outside, objects=dict(artifact=outside)), context='scientific_payload')
    assert {row['path'] for row in closure.entries.values()} == {
        failure['path'], *(proof['path'] for proof in objects.values())}
    child = tmp_path / 'child'
    inherited = _copy_closure(root, closure, child)
    assert originals.resolve_ref(inherited, failure).read_bytes() == failure_bytes
    for proof in objects.values():
        assert originals.resolve_ref(inherited, proof).stat().st_size == proof['size_bytes']
    assert not (child / 'candidate-audit').exists()
    with pytest.raises(ValueError, match='outside the frozen fork closure'):
        originals.resolve_ref(inherited, outside)
