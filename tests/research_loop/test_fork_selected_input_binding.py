"""Exact inherited generated inputs retain their declared physical originals."""
import pytest

from execution_fixtures import execution_registry

from scidiscovery import archive_io
from scidiscovery.research_loop import contracts as c, experiments, fork_sources, originals


@pytest.fixture
def selected_page(tmp_path):
    """A copied task control and one real generated page; no execution grant."""
    root = tmp_path / 'producer'
    path = 'project/nodes/003/inputs/history/index.json'
    actual = root / path
    c.write(actual, dict(nodes=[], limitation='No scientific conclusion is asserted.'))
    material = dict(path='inputs/history/index.json', source=str(actual),
        size_bytes=actual.stat().st_size)
    job_path = 'jobs/003.json'
    c.write(root / job_path, dict(step=3, history=dict(inputs=[material])))
    identity = dict(goal_id='selected-page-fixture', settings_ref=dict(path=str(root / 'settings.json')))
    store = tmp_path / 'child' / originals.FORK_DIRECTORY

    def entry(relative, visibility):
        return dict(origin_identity=identity, path=relative,
            size_bytes=(root / relative).stat().st_size, mode=0o400,
            storage_path=f'inherited/{originals.identity_key(identity)}/{relative}',
            visibility=visibility)

    control = entry(job_path, 'host_proof')
    archive_io.copy_original(root / job_path, store / control['storage_path'], dict(control, kind='file'))

    def source(visibility='scientific_input'):
        entries = [control, *([entry(path, visibility)] if visibility is not None else [])]
        snapshot = originals._snapshot(dict(entries=entries, origins={originals.identity_key(identity):
            dict(identity=identity, original_root=str(root))}))
        return originals._snapshot_source(store, snapshot, identity, 3)

    return root, path, actual, material, source


@pytest.mark.parametrize('visibility', ['scientific_input', 'historical_science'])
def test_inherited_self_source_uses_exact_selected_original(selected_page, visibility):
    root, path, actual, material, source = selected_page
    owner = source(visibility)
    proof = dict(experiments.proof(actual), path=path)
    (root / 'jobs/003.json').unlink()
    assert originals.resolve_ref(owner, proof) == actual
    assert originals.source_job(owner)['history']['inputs'] == [material]
    entry = owner._index[(originals.identity_key(owner.origin_identity), path)][0]
    assert not (owner.storage_root / entry['storage_path']).exists()


@pytest.mark.parametrize('visibility', [None, 'host_proof', 'effect_only', 'private_snapshot'])
def test_self_source_requires_exact_scientific_membership(selected_page, visibility):
    root, path, _, _, source = selected_page
    with pytest.raises(ValueError, match='Selected scientific input original is unavailable'):
        originals._logical_input(root, path, source=source(visibility))


def test_missing_inherited_self_source_does_not_reconstruct_or_copy(selected_page):
    _, path, actual, _, source = selected_page
    owner = source()
    actual.unlink()
    with pytest.raises(FileNotFoundError):
        originals.resolve_ref(owner, dict(path=path))
    assert not actual.exists()


def test_inherited_self_source_retains_saved_original_identity(selected_page):
    _, path, actual, _, source = selected_page
    owner = source()
    proof = dict(experiments.proof(actual), path=path)
    replacement = actual.with_name('replacement.json')
    replacement.write_bytes(actual.read_bytes())
    replacement.replace(actual)
    with pytest.raises(ValueError, match='identity changed'):
        originals.resolve_ref(owner, proof)


@pytest.mark.parametrize('changed', ['entry', 'reference'])
def test_inherited_self_source_retains_exact_size_checks(selected_page, changed):
    _, path, actual, _, source = selected_page
    owner = source()
    ref = dict(path=path, size_bytes=actual.stat().st_size)
    if changed == 'entry':
        owner._index[(originals.identity_key(owner.origin_identity), path)][0]['size_bytes'] += 1
        ref.pop('size_bytes')
        message = 'Original size changed'
    else:
        ref['size_bytes'] += 1
        message = 'differs from its frozen binding'
    with pytest.raises(ValueError, match=message):
        originals.resolve_ref(owner, ref)


def test_missing_local_self_source_stays_unavailable(selected_page):
    root, path, actual, _, source = selected_page
    inherited = source()
    local = originals.NodeSource(inherited.origin_identity, 3, str(root), root)
    actual.unlink()
    with pytest.raises((FileNotFoundError, ValueError)):
        originals.resolve_ref(local, dict(path=path))


@pytest.fixture
def declared_reused_page(tmp_path):
    """A selected task declaration whose page source belongs to an earlier task."""
    root = tmp_path / 'producer'
    actual_path = 'project/nodes/002/inputs/history/nodes/001/0001.json'
    actual = root / actual_path
    c.write(actual, dict(text='Exact existing history page.'))
    row = dict(path='inputs/history/nodes/001/0001.json', source=str(actual),
        size_bytes=actual.stat().st_size)
    alias = 'project/nodes/003/' + row['path']
    identity = dict(goal_id='reused-page-producer', settings_ref=dict(path=str(root / 'settings.json')))
    selected = originals.NodeSource(identity, 3, str(root), root)
    c.write(root / 'jobs/003.json', dict(step=3, history=dict(inputs=[row])))
    return root, actual_path, actual, row, alias, selected


def test_selected_page_closure_keeps_alias_and_actual_source(declared_reused_page):
    root, actual_path, actual, row, alias, selected = declared_reused_page
    closure = fork_sources._Closure()
    closure.add(selected, alias, proof=row, visibility='scientific_input')
    closure.science_ref(selected, row)
    assert closure.entry(selected.origin_identity, alias)['visibility'] == 'scientific_input'
    assert closure.entry(selected.origin_identity, actual_path)['visibility'] == 'historical_science'
    assert closure.paths == {}  # Scientific locators do not schedule copies.
    assert not (root / alias).exists() and actual.is_file()


def bound_page_owner(tmp_path, declared, *, ambiguous=False):
    root, _, actual, row, _, parent = declared
    child = tmp_path / 'child'
    identity = dict(goal_id='reused-page-child', settings_ref=dict(path=str(child / 'settings.json')))
    owner = originals.NodeSource(identity, 3, str(child), child)
    origins = {originals.identity_key(parent.origin_identity):
        dict(identity=parent.origin_identity, original_root=str(root))}
    if ambiguous:
        other = dict(goal_id='overlapping-page-origin',
            settings_ref=dict(path=str(root / 'project/settings.json')))
        origins[originals.identity_key(other)] = dict(identity=other, original_root=str(root / 'project'))
    c.write(child / originals.MANIFEST, dict(version=2, kind='scientific_fork', entries=[], origins=origins))
    c.write(child / 'jobs/003.json', dict(step=3, history=dict(inputs=[row])))
    return owner, actual


def test_selected_page_closure_maps_bound_ancestor_origin(tmp_path, declared_reused_page):
    root, actual_path, _, row, alias, parent = declared_reused_page
    selected, _ = bound_page_owner(tmp_path, declared_reused_page)
    closure = fork_sources._Closure()
    closure.add(selected, alias, proof=row, visibility='scientific_input')
    closure.science_ref(selected, row)
    assert closure.entry(selected.origin_identity, alias)['visibility'] == 'scientific_input'
    assert closure.entry(parent.origin_identity, actual_path)['visibility'] == 'historical_science'
    assert closure.origins[originals.identity_key(parent.origin_identity)]['original_root'] == str(root)
    assert closure.paths == {}


def test_selected_page_closure_leaves_unbound_external_source_typed(tmp_path, declared_reused_page):
    _, actual_path, _, row, alias, parent = declared_reused_page
    child = tmp_path / 'unbound-child'
    child.mkdir()
    identity = dict(goal_id='unbound-page-child', settings_ref=dict(path=str(child / 'settings.json')))
    selected = originals.NodeSource(identity, 3, str(child), child)
    closure = fork_sources._Closure()
    closure.add(selected, alias, proof=row, visibility='scientific_input')
    closure.science_ref(selected, row)
    assert closure.entry(selected.origin_identity, alias)['visibility'] == 'scientific_input'
    assert closure.entry(parent.origin_identity, actual_path, required=False) is None


def test_selected_page_closure_rejects_ambiguous_ancestor_origin(tmp_path, declared_reused_page):
    _, _, _, row, _, _ = declared_reused_page
    selected, _ = bound_page_owner(tmp_path, declared_reused_page, ambiguous=True)
    with pytest.raises(ValueError, match='no unique exact original origin'):
        fork_sources._Closure().science_ref(selected, row)
