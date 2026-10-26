"""Current compiler controls travel by exact references without a legacy index."""
import shutil

from scidiscovery.research_loop import contracts as c, fork_sources, originals, protocol


def test_selected_current_compiler_keeps_opaque_author_and_package_refs(tmp_path):
    root = tmp_path / 'parent'
    identity = dict(goal_id='current-compiler', settings_ref=dict(path=str(root/'settings.json')))
    job = dict(step=2, role='W', handoff_protocol=protocol.CURRENT,
        worker_view_version=1, lineage_projection_version=1)
    def write(name, value):
        path = root/name
        c.write(path, value)
        return dict(path=name, size_bytes=path.stat().st_size)
    job_ref = write('jobs/002.json', job)
    author = write('native-evidence/002/compiled-implementations/original/author.json',
        dict(scientific_packet='Opaque current scientific author source'))
    output = write('host-runtime/experiment-control/002/implementations/I001/package.json',
        dict(schema_version=3, scientific_payload=True))
    compiler = write('native-evidence/002/compiled-implementations/original/compiler.json',
        dict(version=1, job=job_ref, author=author, output=output))
    source = originals.NodeSource(identity, 2, str(root), root)
    closure = fork_sources._Closure()
    closure.compilations(source)
    entries = list(closure.entries.values())
    assert {job_ref['path'], compiler['path'], author['path'], output['path']} <= {row['path'] for row in entries}
    # Inherited selected tasks collect current controls from their bound entries,
    # even after the original host namespace disappears.
    store = tmp_path/'child'/originals.FORK_DIRECTORY
    for entry in entries:
        if entry['visibility'] in {'scientific_input', 'historical_science'}:
            continue
        target = store/entry['storage_path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root/entry['path'], target)
    snapshot = originals._snapshot(dict(entries=entries,
        origins={originals.identity_key(identity): dict(identity=identity, original_root=str(root))}))
    inherited = originals.NodeSource(identity, 2, str(root), store, snapshot=snapshot)
    shutil.rmtree(root)
    retained = fork_sources._Closure()
    retained.compilations(inherited)
    assert {row['path'] for row in retained.entries.values()} == {row['path'] for row in entries}
