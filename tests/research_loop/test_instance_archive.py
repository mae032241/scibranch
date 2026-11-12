"""Retired package entry points and direct sealed-node consumers; no native runs."""
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


def sealed_scene(tmp_path, monkeypatch):
    from test_direct_input_bindings import stopped_seals
    from test_history_handoff import finish
    root, fixture_bytes = scene(tmp_path)
    work = c.workspace(root, 1)
    material = work / 'artifacts/writer.lock'
    material.parent.mkdir(parents=True)
    material.write_bytes(fixture_bytes.read_bytes())
    nested = work / 'experiments/bounded/artifacts/nested.txt'
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b'Exact nested scientific bytes.\n')
    stopped_seals(monkeypatch)
    finish(root, 1, 'H', [], action='experiment')
    # Keep the historical helper's return shape without producing a package.
    return root, tmp_path / 'archive', material, nested


@pytest.mark.parametrize('name', ['archive_export', 'archive_inspect', 'archive_restore_material'])
def test_archive_public_apis_are_unavailable(name):
    assert not hasattr(archive, name)


@pytest.mark.parametrize('command', ['archive-export', 'archive-inspect', 'archive-restore-material'])
def test_archive_commands_reject_before_touching_originals(tmp_path, capsys, command):
    root = tmp_path / 'absent-instance'
    output = tmp_path / 'new-package'
    package = tmp_path / 'historical-package'
    package.mkdir()
    original = package / 'archive.json'
    raw = b'Existing historical package bytes stay untouched.\n'
    original.write_bytes(raw)
    options = {
        'archive-export': ['--root', str(root), '--output', str(output)],
        'archive-inspect': ['--package', str(package)],
        'archive-restore-material': ['--root', str(root), '--package', str(package), '--step', '1', '--index', '1'],
    }
    with pytest.raises(SystemExit) as error:
        cli.main([command, *options[command]])
    assert error.value.code == 2
    assert 'invalid choice' in capsys.readouterr().err
    assert not root.exists() and not output.exists()
    assert original.read_bytes() == raw
    assert [path.name for path in package.iterdir()] == ['archive.json']


def test_help_has_no_archive_or_cold_package_commands(capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(['--help'])
    assert error.value.code == 0
    help_text = capsys.readouterr().out
    for name in ('archive-export', 'archive-inspect', 'archive-restore-material',
                 'checkpoint-enroll', 'checkpoint-export', 'checkpoint-restore'):
        assert name not in help_text
    assert 'execution-recover' in help_text
    assert 'execution-resume' in help_text
    assert 'execution-preparation-maintain' in help_text


def test_sealed_node_views_and_successor_reuse_originals_without_package(tmp_path, monkeypatch):
    from scidiscovery.research_loop import instance_science as science, originals
    root, package, material, nested = sealed_scene(tmp_path, monkeypatch)
    record = c.host_record(c.workspace(root, 1))
    paths = (material, nested, record / 'science-output.json', record / 'result-identity.json')
    saved = {path: (path.stat().st_dev, path.stat().st_ino, path.stat().st_mode, path.read_bytes())
             for path in paths}
    successor = c.build_job(root, 2, 'W', [1])
    selected = next(row for row in successor['direct_materials']
                    if row.get('original_source', row['source']) == str(material))
    assert not (c.workspace(root, 2) / selected['path']).exists()
    # The existing handoff and views do not consume this old authoring input.
    objective = root / c.settings(root)['global_materials'][0]['source']
    objective.unlink()
    with science.science(root, 1) as original:
        assert original.stream.read() == saved[record / 'science-output.json'][3]
    members = science.materials(root, 1)
    member = next(row for row in members['items'] if row['name'] == 'writer.lock')
    original, name = science.artifact(root, 1, member['index'])
    with original:
        assert name == 'writer.lock'
        assert original.stream.read() == saved[material][3]
    assert originals.material_path(root, selected) == material
    assert {path: (path.stat().st_dev, path.stat().st_ino, path.stat().st_mode, path.read_bytes())
            for path in paths} == saved
    assert not package.exists()


@pytest.mark.parametrize('legacy', [False, True])
def test_arbitrary_managed_writer_lease_blocks_lifecycle_without_domain_names(tmp_path, legacy):
    import fcntl
    import os
    from scidiscovery.research_loop import fork_sources, instance_delete
    root, material = scene(tmp_path)
    records = root / 'project/nodes/001/work/lineage'
    records.mkdir(parents=True)
    lease = records / ('custom-adapter.lock' if legacy else 'record-writer.lock')
    descriptor = os.open(lease, os.O_CREAT | os.O_RDONLY, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        c.build_job(root, 1, 'H', [])
        job_path = root / 'jobs/001.json'
        job = c.read(job_path)
        job.pop('worker_view_version', None)
        c.write(job_path, job)
        with pytest.raises(BlockingIOError):
            with fork_sources.source_guard(root):
                pass
        settings = c.settings(root)
        with pytest.raises(archive.files.ArchiveError, match='owner is active'):
            instance_delete.delete_instance(root, instance_id=settings['goal_id'],
                settings_ref=c.record_ref(root / 'settings.json'), operation_id='blocked-delete')
        assert root.exists() and material.exists()
    finally:
        os.close(descriptor)
