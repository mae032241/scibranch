"""Small package and original-material checks; no model, HTTP or backend."""
import json

import pytest

from scidiscovery.research_loop import contracts as c, controller, cli
from scidiscovery.research_loop import instance_archive as archive


def scene(tmp_path):
    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'objective.md'; objective.write_text('Preserve exact original bytes and limitations.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-not-executed'], max_tokens=1000,
        max_seconds=60, max_steps=3, model='fixture', reasoning_effort='high',
        search_policy=None, experiment_mode='external')
    material = root / 'project/nodes/001/work/artifacts/writer.lock'
    material.parent.mkdir(parents=True); material.write_bytes(b'An ordinary scientific filename, not an owner lock.\n')
    return root, material


def entries(package):
    return [json.loads(line) for line in (package / 'manifest.jsonl').read_bytes().splitlines()]


def rewrite_manifest(package, values):
    # Update the outer hash so the real semantic/path consumer, rather than only
    # the manifest checksum, must reject the deliberate malformed package.
    (package / 'manifest.jsonl').write_bytes(b''.join(archive.files.canonical(value) + b'\n' for value in values))
    meta = json.loads((package / 'archive.json').read_bytes())
    proof = archive.files.hash_file(package / 'manifest.jsonl')
    meta['manifest'] = {key: proof[key] for key in ('sha256', 'size_bytes')}
    archive.files.durable_json(package / 'archive.json', meta)


def test_export_retains_scope_and_gaps_without_live_layout_or_credentials(tmp_path, capsys):
    root, material = scene(tmp_path)
    # These are archival bytes, not asserted valid correction/approval facts.
    c.write(root / 'correction-ledger/000001.json', {'historical_fixture_only': True})
    c.write(root / 'execution-authority/host-key-source.json', {'private_locator_fixture': 'never-copy-this'})
    private = root / 'native-homes/001/cache/credentials'; private.parent.mkdir(parents=True)
    private.write_bytes(b'never-copy-native-credential')
    unknown = root / 'undeclared-store/secret'; unknown.parent.mkdir()
    unknown.write_bytes(b'never-recursively-copy-unknown-store')
    # public_root may be unavailable; its original reference stays explicit.
    for source in (tmp_path / 'public').iterdir():
        source.unlink()
    (tmp_path / 'public').rmdir()
    package = tmp_path / 'archive'
    result = archive.archive_export(root, package)
    assert result['restorable'] is False and result['gaps'] == 1
    rows = entries(package); by_path = {row['path']: row for row in rows}
    name = material.relative_to(root).as_posix()
    assert by_path[name]['kind'] == 'file' and (package / by_path[name]['blob']).read_bytes() == material.read_bytes()
    assert by_path['correction-ledger/000001.json']['kind'] == 'file'
    assert by_path['native-homes']['kind'] == 'excluded'
    assert by_path['execution-authority/host-key-source.json']['kind'] == 'excluded'
    assert by_path['undeclared-store']['kind'] == 'gap' and 'undeclared-store/secret' not in by_path
    assert not (package / 'settings.json').exists() and not (package / 'jobs').exists()
    before = {path.name for path in package.iterdir()}
    with pytest.raises((OSError, ValueError)):
        controller.run(package)
    assert {path.name for path in package.iterdir()} == before
    first = archive.archive_inspect(package, limit=1)
    assert first['entries'][0]['index'] == 1 and first['next_after'] == 1
    second = archive.archive_inspect(package, after=first['next_after'], limit=1)
    assert second['entries'][0]['index'] == 2
    dependencies = archive.archive_inspect(package, section='dependencies')['entries']
    assert any(row['owner'] == 'public_root' and row['coverage'] == 'external_tree_not_copied' for row in dependencies)
    assert cli.main(['archive-inspect', '--package', str(package), '--limit', '1']) == 0
    assert json.loads(capsys.readouterr().out)['restorable'] is False
    unchanged = (package / 'manifest.jsonl').read_bytes()
    with pytest.raises(archive.files.ArchiveError, match='already exists'):
        archive.archive_export(root, package)
    assert (package / 'manifest.jsonl').read_bytes() == unchanged


def test_active_writer_and_unknown_native_preserve_originals_without_export(tmp_path):
    root, material = scene(tmp_path); package = tmp_path / 'archive'
    original = material.read_bytes()
    with controller._writer(root):
        with pytest.raises(RuntimeError, match='running controller'):
            archive.archive_export(root, package)
    from scidiscovery.research_loop.instance_ui import _host_lock
    with _host_lock(root):
        with pytest.raises(archive.files.ArchiveError, match='owner is active'):
            archive.archive_export(root, package)
    from scidiscovery.research_loop.experiments import Records
    with Records(dict(record_dir=str(root / 'project/nodes/001/work/lineage'))).lease():
        with pytest.raises(archive.files.ArchiveError, match='owner is active'):
            archive.archive_export(root, package)
    assert not package.exists() and material.read_bytes() == original
    c.build_job(root, 1, 'H', [])
    c.write(root / 'native-evidence/001/native-intent.json', dict(task_sha256=c.digest(root / 'jobs/001.json')))
    with pytest.raises(archive.files.ArchiveError, match='outcome is absent'):
        archive.archive_export(root, package)
    assert not package.exists() and material.read_bytes() == original


def test_export_preserves_pending_configuration_and_its_dependencies(tmp_path):
    from scidiscovery.research_loop import configuration_updates, research_config
    root, _ = scene(tmp_path)
    document = research_config.to_document(root, c.settings(root))
    document['agent']['launcher'] = ['fixture-next-launcher']
    configuration_updates.submit(root, document, expected_revision=0)
    private = root / 'host-control/unrecognized/private.json'
    c.write(private, {'must_not_be_copied': True})
    original = root / 'host-control/configuration/revisions/000001.json'
    with configuration_updates._writer(root):
        with pytest.raises(archive.files.ArchiveError, match='owner is active'):
            archive.archive_export(root, tmp_path / 'busy-archive')
    package = tmp_path / 'archive'
    archive.archive_export(root, package)
    rows = {row['path']: row for row in entries(package)}
    retained = rows[original.relative_to(root).as_posix()]
    assert retained['kind'] == 'file'
    assert (package / retained['blob']).read_bytes() == original.read_bytes()
    assert rows['configuration.lock']['kind'] == 'excluded'
    assert rows['host-control/unrecognized']['kind'] == 'gap'
    assert private.relative_to(root).as_posix() not in rows
    dependencies = archive.archive_inspect(package, section='dependencies')['entries']
    assert any(row['owner'] == 'configuration/1/native_launcher'
               and row['reference'] == ['fixture-next-launcher'] for row in dependencies)


def test_copy_race_never_publishes_or_rolls_back_original_changes(tmp_path, monkeypatch):
    root, material = scene(tmp_path); package = tmp_path / 'archive'
    copy = archive.files.copy_verified

    def concurrent_write(source, destination, entry):
        copy(source, destination, entry)
        if source == material:
            source.write_bytes(b'Concurrent original writer changed these bytes.\n')
            (source.parent / 'new-original.txt').write_bytes(b'New original must not disappear silently.\n')

    monkeypatch.setattr(archive.files, 'copy_verified', concurrent_write)
    with pytest.raises(archive.files.ArchiveError, match='scope changed|scope gained'):
        archive.archive_export(root, package)
    assert not package.exists() and material.read_bytes().startswith(b'Concurrent original')
    assert (material.parent / 'new-original.txt').exists()
    assert not list(tmp_path.glob('.archive-archive-*'))


def test_inspect_rejects_traversal_duplicate_blob_changes_and_symlinks(tmp_path):
    root, _ = scene(tmp_path); package = tmp_path / 'archive'
    archive.archive_export(root, package)
    original = entries(package)
    bad = [dict(row) for row in original]; bad[0]['path'] = '../../outside'
    rewrite_manifest(package, bad)
    with pytest.raises(archive.files.ArchiveError, match='canonical'):
        archive.archive_inspect(package)
    bad = [dict(row) for row in original]; bad[1]['path'] = bad[0]['path']
    rewrite_manifest(package, bad)
    with pytest.raises(archive.files.ArchiveError, match='Duplicate'):
        archive.archive_inspect(package)
    rewrite_manifest(package, original)
    row = next(row for row in original if row['kind'] == 'file')
    blob = package / row['blob']; raw = blob.read_bytes(); blob.chmod(0o600)
    blob.write_bytes(b'Corrupted blob')
    blob.chmod(row['mode'])
    with pytest.raises(archive.files.ArchiveError, match='blob changed'):
        archive.archive_inspect(package)
    blob.unlink(); outside = tmp_path / 'outside'; outside.write_bytes(raw); blob.symlink_to(outside)
    with pytest.raises(archive.files.ArchiveError, match='symbolic links'):
        archive.archive_inspect(package)


def sealed_scene(tmp_path, monkeypatch):
    from test_history_handoff import finish
    root, archival_bytes = scene(tmp_path)
    # The older archive-only fixture includes arbitrary bytes, not node outputs.
    # Scientific outputs use the real workspace owner, without another /work/.
    work = c.workspace(root, 1)
    material = work / 'artifacts/writer.lock'
    material.parent.mkdir(parents=True); material.write_bytes(archival_bytes.read_bytes())
    nested = work / 'experiments/bounded/artifacts/nested.txt'
    nested.parent.mkdir(parents=True); nested.write_bytes(b'Exact nested scientific bytes.\n')
    original_seal = c.seal_result

    def cleaned_seal(root, step):
        # Synthetic native transport only; the actual seal/archive/restore paths run.
        record = c.host_record(c.workspace(root, step))
        outcome = c.read(record / 'native-outcome.json')
        c.write(record / 'native-outcome.json', dict(outcome, cleanup='observed_group_cleaned'))
        return original_seal(root, step)

    monkeypatch.setattr(c, 'seal_result', cleaned_seal)
    finish(root, 1, 'H', [], action='experiment')
    package = tmp_path / 'archive'
    archive.archive_export(root, package)
    return root, package, material, nested


def test_restore_exact_missing_scientific_files_keeps_current_control_and_budget(tmp_path, monkeypatch, capsys):
    from scidiscovery.research_loop import budget, instance_science as science
    root, package, material, nested = sealed_scene(tmp_path, monkeypatch)
    # This real newer event must survive use of the older archive unchanged.
    budget.amend(root, max_tokens=1100, authorization_note='Explicit newer operator ceiling; not restored from archive')
    expected = {path: path.read_bytes() for path in (material, nested)}
    control = {path.relative_to(root): path.read_bytes() for path in root.rglob('*')
        if path.is_file() and path not in expected}
    for path in expected:
        path.unlink()
    def forbidden(*args, **kwargs):
        pytest.fail('Material restoration must not sample current budget or automatically continue research')
    monkeypatch.setattr(budget, 'snapshot', forbidden)
    monkeypatch.setattr(controller, '_run', forbidden)
    members = science.materials(root, 1)['items']
    for path, raw in expected.items():
        member = next(row for row in members if row['name'] == ('writer.lock' if path == material else 'experiments/bounded/artifacts/nested.txt'))
        args = ['archive-restore-material', '--root', str(root), '--package', str(package),
                '--step', '1', '--index', str(member['index'])]
        assert cli.main(args) == 0
        assert json.loads(capsys.readouterr().out)['status'] == 'material_restored'
        original, _ = science.artifact(root, 1, member['index'])
        with original:
            assert original.stream.read() == raw
        assert cli.main(args) == 0
        assert json.loads(capsys.readouterr().out)['status'] == 'material_already_present'
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob('*')
        if path.is_file() and path not in expected} == control
    assert not list((root / 'host-runtime').glob('material-restore-*'))


def test_restore_rejects_conflicts_wrong_seal_and_control_targets(tmp_path, monkeypatch):
    from scidiscovery.research_loop import instance_science as science
    root, package, material, _ = sealed_scene(tmp_path, monkeypatch)
    index = next(row['index'] for row in science.materials(root, 1)['items'] if row['name'] == 'writer.lock')
    original = material.read_bytes()
    material.chmod(0o600)  # Deliberate fixture corruption, not production permission changes.
    material.write_bytes(b'Current different original must survive.')
    with pytest.raises(archive.files.ArchiveError, match='never overwritten'):
        archive.archive_restore_material(root, package, step=1, index=index)
    assert material.read_bytes() == b'Current different original must survive.'
    material.unlink()
    outside = tmp_path / 'outside'; outside.write_bytes(original); material.symlink_to(outside)
    with pytest.raises(archive.files.ArchiveError, match='symbolic links'):
        archive.archive_restore_material(root, package, step=1, index=index)
    assert outside.read_bytes() == original
    material.unlink()
    seal_path = c.host_record(c.workspace(root, 1)) / 'result-identity.json'
    sealed = seal_path.read_bytes(); mode = seal_path.stat().st_mode & 0o777; value = c.read(seal_path)
    c.write(seal_path, dict(value, scope='A different producer seal is not the archived producer'))
    with pytest.raises(archive.files.ArchiveError, match='exact current producer seal'):
        archive.archive_restore_material(root, package, step=1, index=index)
    seal_path.chmod(0o600); seal_path.write_bytes(sealed); seal_path.chmod(mode)
    changed = c.read(seal_path)
    changed['materials'][index-1]['source'] = str(root / 'settings.json')
    c.write(seal_path, changed)
    with pytest.raises(archive.files.ArchiveError, match='call and control originals are excluded'):
        archive.archive_restore_material(root, package, step=1, index=index)
    seal_path.chmod(0o600); seal_path.write_bytes(sealed); seal_path.chmod(mode)
    with pytest.raises(archive.files.ArchiveError, match='index'):
        archive.archive_restore_material(root, package, step=1, index=0)
    assert not material.exists()


def test_restore_refuses_active_unknown_and_competing_originals(tmp_path, monkeypatch):
    from scidiscovery.research_loop import instance_science as science
    root, package, material, _ = sealed_scene(tmp_path, monkeypatch)
    index = next(row['index'] for row in science.materials(root, 1)['items'] if row['name'] == 'writer.lock')
    material.unlink()
    with controller._writer(root):
        with pytest.raises(RuntimeError, match='running controller'):
            archive.archive_restore_material(root, package, step=1, index=index)
    outcome_path = c.host_record(c.workspace(root, 1)) / 'native-outcome.json'
    outcome = outcome_path.read_bytes()
    c.write(outcome_path, dict(c.read(outcome_path), cleanup='cleanup_unknown'))
    with pytest.raises(archive.files.ArchiveError, match='cleanup is unconfirmed'):
        archive.archive_restore_material(root, package, step=1, index=index)
    assert not material.exists()
    outcome_path.write_bytes(outcome)
    link = archive.os.link
    def competing_file(source, destination, **kwargs):
        if destination == material:
            material.write_bytes(b'A competing original, never overwritten.')
        return link(source, destination, **kwargs)
    monkeypatch.setattr(archive.os, 'link', competing_file)
    with pytest.raises(archive.files.ArchiveError, match='never overwritten'):
        archive.archive_restore_material(root, package, step=1, index=index)
    assert material.read_bytes() == b'A competing original, never overwritten.'
    assert not list((root / 'host-runtime').glob('material-restore-*'))
