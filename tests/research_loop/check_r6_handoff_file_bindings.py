"""Exact file relationships complement the r6 zero-old-verification checks.

The oracle saves tiny current originals as each real fixture seal closes. It is
deliberately test-only: production handoffs need not revalidate that prefix.
No model, solver, real dataset, synthetic HOME or execution replay is added.
Run this check_ file by its exact path; it is outside default collection.
"""
from collections import Counter
from contextvars import Context
from copy import deepcopy
from pathlib import Path

import pytest

from scidiscovery.research_loop import (
    accepted_prefix, contracts as c, controller, experiment_client, fork_runtime, fork_service, handoff_state,
    history, lineage, originals, shared_input_views,
)
from execution_fixtures import execution_registry
from r6_handoff_fixture import r6_cost_scene
from test_history_handoff import finish


def _saved(path):
    path = Path(path)
    info = path.stat()
    return dict(path=path, data=path.read_bytes(), size_bytes=info.st_size,
        file_stat=[info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns])


def _unchanged(saved):
    assert _saved(saved['path']) == saved


def _exact_proof(root, descriptor, saved, *, snapshot=False):
    assert root / descriptor['snapshot' if snapshot else 'path'] == saved['path']
    assert descriptor['size_bytes'] == saved['size_bytes']
    assert descriptor['file_stat'] == saved['file_stat']
    _unchanged(saved)


@pytest.fixture
def r6_bound_scene(request, monkeypatch):
    """Capture authoritative current-call files before any successor exists."""
    saved = {}
    seal_result = c.seal_result

    def capture(root, step, *args, **kwargs):
        artifact = c.workspace(root, step) / 'artifacts/binding.txt'
        # Identical basenames deliberately have different authors and bytes.
        assert not artifact.exists()
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(f'Exact synthetic original from node {step:03d}.\n')
        result = seal_result(root, step, *args, **kwargs)
        seal = c.read(root / f'native-evidence/{step:03d}/result-identity.json')
        raw = root / f'native-evidence/{step:03d}/science-output.json'
        _exact_proof(root, seal['raw_ref'], _saved(raw))
        node = dict(raw=_saved(raw), artifact=_saved(artifact), calls={})
        if seal.get('observations'):
            observed = c.read(root / seal['observations']['path'])
            for row in observed.get('managed_calls', []):
                directory = experiment_client.record_directory(root, step) / 'calls' / row['call_id']
                files = {field: _saved(directory / name) for field, name in (
                    ('accepted', 'accepted.json'), ('completed', 'completed.json'),
                    ('terminal', 'terminal.json'), ('observation', 'execution-observation.json'))
                    if row.get(field) is not None}
                accepted = c.read(files['accepted']['path'])
                native = c.read(files['observation']['path'])
                assert accepted['native_attempt']['number'] == 0
                assert accepted['call_id'] == row['call_id']
                assert accepted['scope']['step'] == step
                for field in ('accepted', 'completed'):
                    _exact_proof(root, native[field], files[field])
                outputs = {item['name']: _saved(root / item['path'])
                    for item in native.get('scientific_outputs', native['outputs'])}
                node['calls'][row['call_id']] = dict(files=files, outputs=outputs,
                    scope=deepcopy(accepted['scope']),
                    mechanism_binding=deepcopy(accepted['mechanism_binding']))
        saved[step] = node
        return result

    monkeypatch.setattr(c, 'seal_result', capture)
    root, evidence, configurations = request.getfixturevalue('r6_cost_scene')
    assert len(saved) == len(evidence['nodes']) == 8
    assert sum(len(row['calls']) for row in saved.values()) == 6
    return root, evidence, configurations, saved


def _sealed_relationships(root, saved, before_step):
    projection = lineage.project(root, before_step)
    seen = []
    for job, payload, seal, _ in projection['sources']:
        step = job['step']
        expected = saved[step]
        _exact_proof(root, seal['raw_ref'], expected['raw'])
        assert payload['scientific_result'] == c.read(expected['raw']['path'])['scientific_result']
        artifact = next(row for row in seal['materials']
            if row['path'] == f'inputs/parents/{step:03d}/artifacts/binding.txt')
        assert Path(artifact['source']) == expected['artifact']['path']
        assert artifact['size_bytes'] == expected['artifact']['size_bytes']
        assert artifact['file_stat'] == expected['artifact']['file_stat']
        _unchanged(expected['artifact'])
        observed = history.read_observation(root, job, seal)
        rows = [] if observed is None else observed['managed_calls']
        assert {row['call_id'] for row in rows} == set(expected['calls'])
        for row in rows:
            call = expected['calls'][row['call_id']]
            assert row['scope'] == call['scope']
            assert row['mechanism_binding'] == call['mechanism_binding']
            for field, original in call['files'].items():
                _exact_proof(root, row[field], original, snapshot=True)
            assert {item['name'] for item in row.get('outputs', [])} == set(call['outputs'])
            for output in row.get('outputs', []):
                original = call['outputs'][output['name']]
                _exact_proof(root, output, original, snapshot=True)
                logical = (f'inputs/parents/{step:03d}/artifacts/experiments/'
                    f'{row["call_id"]}/artifacts/{output["name"]}')
                material = next(item for item in seal['materials'] if item['path'] == logical)
                assert Path(material['source']) == original['path']
                assert material['file_stat'] == original['file_stat']
            seen.append(call['files']['accepted']['path'])
    # Each of six equal basenames still resolves to its own actual call inode.
    assert len(seen) == len(set(seen)) == 6
    assert Counter(path.name for path in seen) == {'accepted.json': 6}
    assert len({tuple(_saved(path)['file_stat'][:2]) for path in seen}) == 6


def _history_files(root, job):
    """Explicit oracle over the frozen cut, outside the production handoff."""
    prefix = accepted_prefix.for_job(job)
    public = accepted_prefix.visibility(root, prefix)
    ordinals = Path(root) / accepted_prefix.MEMBERS / accepted_prefix._view_key(prefix) / 'ordinals'
    files = {}
    for ordinal in range(1, prefix['count'] + 1):
        link = c.read(c.regular(ordinals / f'{ordinal:09d}.json'))
        member = accepted_prefix.member(root, prefix, link['step'], origin=link['origin_identity'])
        assert member['ordinal'] == ordinal
        directory = Path(member['navigation']['path']).parent
        source = originals.node(root, link['step'])
        seal = c.read(originals.source_record(source, 'result-identity.json'))
        assert member['seal_ref'] == history.source_seal_ref(source)
        for name, proof in (('science.json', seal['result_ref']), ('scientific-original.json', seal['raw_ref'])):
            logical = Path('inputs/history') / directory / name
            alias = public / directory / name
            assert alias.samefile(originals.resolve_ref(source, proof))
            files[logical.as_posix()] = _saved(alias)
    return files


def _readonly_bindings(root, job, expected_history):
    work = c.workspace(root, job['step'])
    arguments = shared_input_views.mount_arguments(root, job, work,
        private=True, include_skills=False, selected_code=[])
    mounts = {Path(arguments[index + 2]): Path(arguments[index + 1])
        for index, argument in enumerate(arguments) if argument == '--ro-bind'}
    public = accepted_prefix.visibility(root, accepted_prefix.for_job(job))
    assert mounts[work / 'inputs/history'] == public
    assert mounts[public] == public
    for logical, saved in expected_history.items():
        relative = Path(logical).relative_to('inputs/history')
        assert (mounts[work / 'inputs/history'] / relative).samefile(saved['path'])
        assert (mounts[public] / relative).samefile(saved['path'])
        _unchanged(saved)
    for material in job['direct_materials']:
        assert mounts[work / material['path']] == Path(material['source'])
    return mounts


@pytest.mark.process_e2e
def test_r6_append_and_cold_restore_preserve_exact_file_relationships(r6_bound_scene):
    root, _, _, saved = r6_bound_scene
    _sealed_relationships(root, saved, 9)
    job9 = c.build_job(root, 9, 'A', [7, 8])
    old_history = _history_files(root, job9)
    assert len(old_history) == 16
    assert len({Path(path).parts[4] for path in old_history}) == 8
    _readonly_bindings(root, job9, old_history)
    # Select an old exact producer even though every node has binding.txt.
    source_seal = c.read(root / 'native-evidence/002/result-identity.json')
    index = next(number for number, row in enumerate(
        sorted(source_seal['materials'], key=lambda item: item['path']), 1)
        if row['path'] == 'inputs/parents/002/artifacts/binding.txt')
    selected_id = f'W002-I{index:03d}'
    finish(root, 9, 'A', [7, 8], action='revise_h', selection=[selected_id],
        search_choice=dict(dispositions=[], next_focus=None))
    job10, _, _ = finish(root, 10, 'E', [9])
    job11 = c.build_job(root, 11, 'E', [10])

    def verify_appended():
        with handoff_state.scope(root):
            _sealed_relationships(root, saved, 11)
            for job, parent in ((job10, 9), (job11, 10)):
                assert job['direct_originals'] == [dict(
                    path=f'inputs/parents/{parent:03d}/conclusion.json',
                    size_bytes=saved[parent]['raw']['size_bytes'])]
                assert len(job['selected_inputs']) == 1
                selected = job['selected_inputs'][0]
                assert selected['label'] == selected_id and selected['origin_step'] == 2
                assert selected['origin_result_ref'] == source_seal['result_ref']
                material = selected['material']
                assert material['path'] == 'inputs/parents/002/artifacts/binding.txt'
                assert originals.material_path(root, material) == saved[2]['artifact']['path']
                _unchanged(saved[2]['artifact'])
                current_history = _history_files(root, job)
                for logical, original in old_history.items():
                    assert current_history[logical] == original
                mounts = _readonly_bindings(root, job, old_history)
                assert mounts[c.workspace(root, job['step']) / material['path']] == saved[2]['artifact']['path']
                c._validate_inputs(root, c.workspace(root, job['step']), job)

    verify_appended()
    # The fixture keeps its controller scope active; a new Context guarantees
    # a cold scope without modifying the implementation's private cache state.
    Context().run(verify_appended)


@pytest.mark.process_e2e
def test_r6_fork_retains_exact_original_origin_and_current_attempt(r6_bound_scene, tmp_path):
    root, _, _, saved = r6_bound_scene
    with controller._writer(root):
        pass
    child = tmp_path / 'binding-child'
    limits = dict(max_tokens=1000, max_seconds=30, max_steps=3)
    preview = fork_service.preview(root, 8, child, budget=limits)
    assert preview['status'] == 'fork_preview_ready', preview
    result = fork_service.create(root, 8, child, budget=limits,
        expected_preview_id=preview['preview_id'], operation_id='r6-exact-file-bindings')
    assert result['state'] == 'paused'
    identity = originals.local_identity(root)
    manifest = c.read(child / originals.MANIFEST)

    def verify_fork():
        with handoff_state.scope(child):
            source = originals.selected_source(child)
            assert source.origin_identity == identity and source.step == 8
            assert source.original_root == str(root)
            for step in range(1, 8):
                owner = originals.node(child, step)
                assert owner.inherited and owner.origin_identity == identity
                assert owner.snapshot is source.snapshot
                assert owner.original_root == str(root)
                # This corpus has one native attempt. The inherited source may
                # never silently select a different attempt or a child alias.
                assert owner.native_record == f'native-evidence/{step:03d}'
                assert originals.source_raw(owner) == saved[step]['raw']['path']
                job, _, seal, _ = history._source(child, step)
                _exact_proof(root, seal['raw_ref'], saved[step]['raw'])
                observed = history.read_observation(child, job, seal)
                calls = [] if observed is None else observed['managed_calls']
                assert {row['call_id'] for row in calls} == set(saved[step]['calls'])
                for row in calls:
                    expected = saved[step]['calls'][row['call_id']]
                    for field, original in expected['files'].items():
                        proof = row[field]
                        assert proof['origin_identity'] == identity
                        _exact_proof(root, proof, original, snapshot=True)
                        relative = original['path'].relative_to(root).as_posix()
                        entries = [item for item in manifest['entries']
                            if item['origin_identity'] == identity and item['path'] == relative]
                        assert len(entries) == 1
                        entry = entries[0]
                        retained = originals.resolve_ref(owner, dict(proof, path=proof['snapshot']))
                        if entry['visibility'] in {'scientific_input', 'historical_science'}:
                            assert retained == original['path']
                        else:
                            # Transported controls retain their exact origin and
                            # member path; their child inode is intentionally new.
                            storage = f'inherited/{originals.identity_key(identity)}/{relative}'
                            assert entry['storage_path'] == storage
                            assert retained == child / originals.FORK_DIRECTORY / storage
                        assert retained.read_bytes() == original['data']
                        assert retained.stat().st_size == original['size_bytes']
                artifact = next(item for item in seal['materials']
                    if item['path'] == f'inputs/parents/{step:03d}/artifacts/binding.txt')
                assert originals.material_path(child, artifact) == saved[step]['artifact']['path']
                _unchanged(saved[step]['artifact'])
            assert not (child / 'jobs/002.json').exists()
            selected_job=fork_runtime.build_selected_job(child,8)
            c._validate_inputs(child,c.workspace(child,8),selected_job)

    Context().run(verify_fork)
