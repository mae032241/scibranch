"""Exact original identity, bounded manifests and immutable copy boundaries."""
import pytest

from scidiscovery.research_loop import contracts as c, fork_sources, originals
from test_fork_source import completed_source
from test_history_handoff import finish
from execution_fixtures import execution_registry
from test_local_input_sources import local_scene
from test_native_execution_inputs import chain


def _copy_completed(root, step, child):
    from scidiscovery.research_loop import controller
    with controller._writer(root):
        pass
    plan = fork_sources.inspect_source(root, step)
    assert plan['blocked_reasons'] == []
    fork_sources.copy_source(plan, child / originals.FORK_DIRECTORY)


@pytest.fixture
def source_identity_records(monkeypatch):
    # These reused fixtures synthesize native headers and never launch a native
    # process. Record that fact before sealing, preserving the sealed originals.
    seal = c.seal_result
    def no_native(root, step):
        path = c.host_record(c.workspace(root, step)) / 'native-outcome.json'
        c.write(path, dict(c.read(path), native_started=False))
        return seal(root, step)
    monkeypatch.setattr(c, 'seal_result', no_native)


@pytest.fixture
def portable_local_scene(source_identity_records, request):
    return request.getfixturevalue('local_scene')


@pytest.fixture
def portable_native_chain(source_identity_records, request):
    return request.getfixturevalue('chain')


def _rewrite_portable_provenance(child, identity, step, document):
    """Keep wrapper descriptors consistent so negatives exercise relation validation."""
    import copy
    manifest = copy.deepcopy(originals._manifest(child))
    store = child / originals.FORK_DIRECTORY
    def replace(path, value):
        entry = next(row for row in manifest['entries'] if row['origin_identity'] == identity and row['path'] == path)
        scratch = store / 'negative-rewrite.json'
        c.write(scratch, value)
        entry['size_bytes'] = scratch.stat().st_size
        entry['storage_path'] = (f'inherited/{originals.identity_key(identity)}/versions/'
            f'{path}')
        target = store / entry['storage_path']
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():target.chmod(0o600)
        target.write_bytes(scratch.read_bytes())
        scratch.unlink()
        return {key:entry[key] for key in ('path', 'size_bytes')}
    owner = originals.node(child, step)
    seal = c.read(originals.source_record(owner, 'result-identity.json'))
    seal['source_provenance'] = replace(seal['source_provenance']['path'], document)
    ref = replace(f'native-evidence/{step:03d}/result-identity.json', seal)
    for row in manifest['prefix_entries']:
        if row['origin_identity'] == identity and row['step'] == step:
            row['seal_ref'] = dict(ref, origin_identity=identity)
    c.write(child / originals.MANIFEST, manifest)
    return originals.node(child, step)


@pytest.mark.parametrize('path', ['../escape', '/absolute', 'a/../b', './file', 'a//b', 'a\\b'])
def test_noncanonical_original_paths_are_rejected(path):
    with pytest.raises(ValueError, match='canonical relative'):
        originals.relative(path)


def test_origin_identity_is_not_content_identity():
    reference = dict(path='/fixture/settings.json')
    assert originals.identity_key(dict(goal_id='first', settings_ref=reference)) != originals.identity_key(
        dict(goal_id='second', settings_ref=reference))


def test_changed_copied_member_does_not_validate_from_manifest_only(tmp_path):
    root = completed_source(tmp_path)
    plan = fork_sources.inspect_source(root, 2)
    child = tmp_path / 'child'
    manifest = fork_sources.copy_source(plan, child / originals.FORK_DIRECTORY)
    entry = next(row for row in manifest['entries'] if row['path'] == 'jobs/001.json')
    path = child / originals.FORK_DIRECTORY / entry['storage_path']
    path.chmod(0o600)
    path.write_text('{}')
    with pytest.raises(ValueError, match='Original size changed'):
        fork_sources.validate_manifest(child, full=True)


def test_copy_rechecks_source_and_never_changes_parent_bytes(tmp_path):
    root = completed_source(tmp_path)
    plan = fork_sources.inspect_source(root, 2)
    source = root / 'jobs/001.json'
    before = source.read_bytes()
    fork_sources.copy_source(plan, tmp_path / 'first')
    assert source.read_bytes() == before
    source.write_text('{}')
    with pytest.raises(Exception):
        fork_sources.copy_source(plan, tmp_path / 'second')


def test_source_symlink_is_not_followed(tmp_path):
    root = completed_source(tmp_path)
    # Sealed controls are read by inspection; the sealed science locator is not.
    source = root / 'native-evidence/001/result-identity.json'
    original = source.read_bytes()
    target = tmp_path / 'foreign.json'
    target.write_bytes(original)
    source.unlink()
    source.symlink_to(target)
    with pytest.raises((ValueError, OSError, RuntimeError)):
        fork_sources.inspect_source(root, 2)


def test_one_source_sequence_shares_one_manifest_snapshot(tmp_path, monkeypatch):
    root = completed_source(tmp_path)
    for step, role, parents, action in [(3, 'H', [1, 2], 'analyze'), (4, 'A', [3], 'continue_w')]:
        _, _, record = finish(root, step, role, parents, action=action)
        c.write(record / 'native-outcome.json', dict(c.read(record / 'native-outcome.json'), native_started=False))
    child = tmp_path / 'child'
    fork_sources.copy_source(fork_sources.inspect_source(root, 4), child / originals.FORK_DIRECTORY)
    reads = []
    read = originals._json
    def tracked(path):
        if path.name == 'manifest.json':
            reads.append(path)
        return read(path)
    monkeypatch.setattr(originals, '_json', tracked)
    sources = originals.source_sequence(child, 4)
    assert len(reads) == 1
    assert len(sources) == 3
    assert all(source.entries is sources[0].entries for source in sources)
    assert all(source._index is sources[0]._index for source in sources)
    assert originals.dependency(sources[-1], 1).entries is sources[0].entries


def test_history_scientific_view_cannot_add_a_file_dependency(tmp_path):
    root = completed_source(tmp_path)
    secret = root / 'project/nodes/999/future.json'
    c.write(secret, dict(future_scientific_result='This is unavailable before N'))
    path = root / 'jobs/002.json'
    job = c.read(path)
    job['history']['view']['arbitrary'] = dict(raw=dict(path=secret.relative_to(root).as_posix(), ))
    c.write(path, job)
    intent = root / 'native-evidence/002/native-intent.json'
    c.write(intent, dict(c.read(intent), task_ref=c.record_ref(path)))
    # Altering the scientific view is not a new source declaration. Either the
    # original task validator rejects the changed projection, or the dependency
    # compiler must leave the typed-unavailable future member out of its closure.
    try:
        plan = fork_sources.inspect_source(root, 2)
    except ValueError:
        return
    assert secret.relative_to(root).as_posix() not in {row['path'] for row in plan['entries']}


def test_missing_source_writer_lock_is_preview_blocked_without_creation(tmp_path):
    root = completed_source(tmp_path)
    (root / 'host-run.lock').unlink()
    plan = fork_sources.inspect_source(root, 2)
    assert any(row['code'] == 'source_guard_unavailable' for row in plan['blocked_reasons'])
    with pytest.raises(ValueError, match='source_guard_unavailable'):
        with fork_sources.source_guard(root):
            pass
    assert not (root / 'host-run.lock').exists()


def test_control_json_parse_limit_is_enforced_before_read(tmp_path, monkeypatch):
    path = tmp_path / 'huge-control.json'
    with path.open('wb') as stream:
        stream.truncate(32 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match='finite.*bound'):
        originals._json(path)
    with pytest.raises(ValueError, match='finite.*bound'):
        fork_sources._json(path)


def test_typed_observer_and_pending_proofs_do_not_parse_scientific_payloads(tmp_path):
    root = completed_source(tmp_path)
    source = originals.node(root, 2)
    archive = root / 'host-history/objects' / ('a' * 64)
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text('retained observer proof')
    proof = dict(path='project/nodes/001/artifacts/evidence.txt',
        size_bytes=archive.stat().st_size, retention='archived',
        snapshot=archive.relative_to(root).as_posix())
    future = root / 'project/nodes/999/future.json'
    c.write(future, dict(secret='future result'))
    document = 'host-history/observations/002.json'
    c.write(root / document, dict(evidence=[dict(originals=[proof])],
        authored_payload=dict(raw=dict(path=future.relative_to(root).as_posix(), ))))
    closure = fork_sources._Closure()
    closure.control(source, dict(path=document))
    paths = {row['path'] for row in closure.entries.values()}
    assert proof['snapshot'] in paths
    # Authored payload text never becomes a control reference.
    assert future.relative_to(root).as_posix() not in paths
    pending = 'native-evidence/002/pending-execution-set.json'
    intent = root / 'native-evidence/002/native-intent.json'
    c.write(root / pending, dict(attempts=[{'native-intent.json': dict(
        path=intent.relative_to(root).as_posix(), )}]))
    closure.control(source, dict(path=pending))
    assert intent.relative_to(root).as_posix() in {row['path'] for row in closure.entries.values()}


@pytest.mark.process_e2e
def test_inherited_same_node_completed_call_keeps_its_exact_parent(tmp_path):
    import shutil
    from scidiscovery.research_loop import candidate_refs, controller, fork_service, lineage
    from test_lineage import scene, cov
    root, config, digest = scene(tmp_path)
    script = c.workspace(root, 2) / 'work/train.py'
    script.write_text('success')
    parent = cov.run(config, script, new=True)
    branch = cov.run(config, script, parent=parent['candidate_id'])
    finish(root, 2, 'W', [1])
    finish(root, 3, 'A', [1, 2], action='continue_w')
    for step in (1, 2, 3):
        path = root / f'native-evidence/{step:03d}/native-outcome.json'
        c.write(path, dict(c.read(path), native_started=False))
    with controller._writer(root):
        pass
    child = tmp_path / 'child'
    limits = dict(max_tokens=1000, max_seconds=30, max_steps=2)
    preview = fork_service.preview(root, 3, child, budget=limits)
    fork_service.create(root, 3, child, budget=limits, expected_preview_id=preview['preview_id'],
        operation_id='same-node-parent')
    source = originals.node(child, 2)
    expected = (candidate_refs.origin_candidate(source.origin_identity, branch['candidate_id']),
        candidate_refs.origin_candidate(source.origin_identity, parent['candidate_id']))
    shutil.rmtree(root)
    view = lineage.build(child, 3)
    assert expected in {(edge['subject'], edge['target']) for edge in view['relations'] if edge['kind'] == 'derivation'}


@pytest.mark.process_e2e
def test_original_review_cannot_qualify_a_new_producer_even_with_identical_material(tmp_path, monkeypatch):
    """A real sealed pass binds producer identity in addition to artifact bytes."""
    import copy
    import sys
    from pathlib import Path
    from scidiscovery.research_loop import controller, formal_review, qualification as q
    from test_execution_authority import prepare
    from test_formal_review import LAUNCHER
    launcher = tmp_path / 'codex'
    launcher.write_text('#!' + sys.executable + '\n' + LAUNCHER)
    launcher.chmod(0o700)
    home = tmp_path / 'native'
    home.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.delenv('REVIEW_REUSE_AUTHOR', raising=False)
    monkeypatch.delenv('REVIEW_MISSING_AUTHOR', raising=False)
    root, _ = prepare(tmp_path, launcher=[str(launcher)])
    assert controller.run(root)['status'] == 'finish'
    review_seal = c.read(root / 'native-evidence/004/result-identity.json')
    receipt = review_seal['formal_review']
    subject = q._read(root, receipt)['subject']
    assert formal_review.require_review(root, subject, receipt) == receipt
    child_area = tmp_path / 'new-child'
    child_area.mkdir()
    child, _ = prepare(child_area, enabled=False)
    finish(child, 1, 'H', [], action='experiment')
    filename = Path(subject['material']['path']).name
    finish(child, 2, 'W', [1], action='wait',
        files={filename: Path(subject['material']['source']).read_text()})
    new_seal = originals.original_ref(originals.node(child, 2), 'native-evidence/002/result-identity.json')
    new_material = next(row for row in c.read(child / new_seal['path'])['materials']
        if Path(row['source']).name == filename)
    actual_child_subject = dict(producer_step=2, producer_seal=new_seal, material=new_material)
    with pytest.raises(ValueError, match='Exact subject'):
        formal_review.require_review(root, actual_child_subject, receipt)
    # Also isolate producer identity while holding the old material descriptor
    # exactly equal: identical path and bytes do not extend the original pass.
    child_subject = copy.deepcopy(subject)
    child_subject['producer_seal'] = new_seal
    with pytest.raises(ValueError, match='Exact subject'):
        formal_review.require_review(root, child_subject, receipt)
    changed_bytes = copy.deepcopy(subject)
    changed_bytes['material']['size_bytes'] += 1
    with pytest.raises(ValueError, match='Exact subject'):
        formal_review.require_review(root, changed_bytes, receipt)


@pytest.mark.process_e2e
def test_portable_local_coverage_recomputes_relations_and_restrictions(portable_local_scene, tmp_path, capsys):
    import copy
    import json
    import shutil
    from scidiscovery.research_loop import experiments as e, formal_review, qualification as q
    from test_local_input_sources import authored
    root, _, _, path = portable_local_scene
    bound = c.workspace(root, 2) / 'inputs/parents/001/artifacts/base'
    assert e.main(['--config', str(path), 'run', '--experiment-dir', str(bound), '--script', str(bound / 'run.py')]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'known_success'
    directory, script = authored(root)
    assert e.main(['--config', str(path), 'run', '--experiment-dir', str(directory), '--script', str(script)]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'known_success'
    script.write_text('raise RuntimeError("later author edit")\n')
    finish(root, 2, 'W', [1], files={'revised.py': 'def predict(): return 43\n'})
    finish(root, 3, 'A', [2], action='continue_w')
    expected_authors = formal_review.authors(root, 2)
    child = tmp_path / 'portable-local'
    _copy_completed(root, 3, child)
    owner = originals.node(child, 2)
    identity = owner.origin_identity
    shutil.rmtree(root)
    c.validate_original(owner)
    assert q.original_status(owner)['restriction'] == 'clear'
    assert formal_review.authors(child, 2) == expected_authors
    job = originals.source_job(owner)
    seal = c.read(originals.source_record(owner, 'result-identity.json'))
    baseline = q._read(child, seal['source_provenance'], source=owner)
    assert len(baseline['managed_input_coverage']) == 2
    for change in ('omitted_coverage', 'omitted_call', 'other_accepted', 'wrong_contract', 'contradictory_missing_reason'):
        changed = copy.deepcopy(baseline)
        if change == 'omitted_coverage':changed.pop('managed_input_coverage')
        elif change == 'omitted_call':changed['managed_input_coverage'].pop()
        elif change == 'other_accepted':
            changed['managed_input_coverage'][0]['accepted_ref'] = changed['managed_input_coverage'][1]['accepted_ref']
        elif change == 'wrong_contract':changed['managed_input_coverage'][0]['input_sources'] = '0' * 64
        else:changed['missing_reason'] = 'observation_input_provenance_unsupported'
        changed_owner = _rewrite_portable_provenance(child, identity, 2, changed)
        # Every retained member descriptor is correct; only the source relationships
        # and the corresponding published restriction are inconsistent.
        fork_sources.validate_manifest(child, full=True)
        with pytest.raises(q.IntegrityError):
            c.validate_original(changed_owner)
    _rewrite_portable_provenance(child, identity, 2, baseline)


def _completed_portable_compiled_source(portable_native_chain, capsys):
    import json
    from scidiscovery.research_loop import experiments as e, formal_review
    from test_native_execution_inputs import _reviewed_execution
    root, backend, output, active, configuration, _ = _reviewed_execution(portable_native_chain, capsys)
    assert e.main(['--config', str(active), 'run', '--payload', str(output)]) == 2
    call = json.loads(capsys.readouterr().out)['call_id']
    backend.state = 'succeeded'
    assert e.main(['--config', str(active), 'reconcile', '--call-id', call]) == 0
    capsys.readouterr()
    finish(root, 6, 'W', [5], files={'revised.py': 'def predict(): return 44\n'})
    finish(root, 7, 'A', [6], action='continue_w')
    expected = formal_review.authors(root, 6)
    assert expected['complete']
    return root, backend, configuration, call, expected


@pytest.mark.process_e2e
def test_portable_compiled_managed_authors_keep_complete_exact_contributors(portable_native_chain, tmp_path, capsys):
    import shutil
    from scidiscovery.research_loop import formal_review, qualification as q
    from scidiscovery.operations import review_rules
    root, _, _, _, expected = _completed_portable_compiled_source(portable_native_chain, capsys)
    child = tmp_path / 'portable-native'
    _copy_completed(root, 7, child)
    shutil.rmtree(root)
    owner = originals.node(child, 6)
    c.validate_original(owner)
    assert q.original_status(owner)['restriction'] == 'clear'
    actual = formal_review.authors(child, 6)
    assert actual == expected
    people = [tuple(row) for row in actual['participants']]
    assert review_rules.independence(people, [people[0]], authors_complete=True, reviewers_complete=True) == 'overlap'


@pytest.mark.process_e2e
@pytest.mark.parametrize('prepared_retention', ['missing', 'reused'])
def test_fork_rejects_invalid_live_prepared_locator_even_with_retained_archive(
        portable_native_chain, tmp_path, capsys, prepared_retention):
    from pathlib import Path
    from scidiscovery.research_loop import controller, experiments as e, history, qualification as q
    root, backend, configuration, call, _ = _completed_portable_compiled_source(portable_native_chain, capsys)
    # A valid live parent is required before any fork inventory is collected.
    c.validate(c.workspace(root, 7))
    records = e.Records(configuration)
    prepared = records.accepted(call)['prepared']
    locator = Path(prepared['local_path'])
    with controller._writer(root), c.record_writer(records.root, exclusive=True):
        retained = history._managed_proof(root, locator, prepared)
        assert retained['retention'] == 'archived'
        assert retained['size_bytes'] == prepared['size_bytes']
        assert (root / retained['snapshot']).stat().st_size == prepared['size_bytes']
        if prepared_retention == 'missing':
            locator.unlink()
        else:
            locator.chmod(0o600)
            locator.write_bytes(b'later bytes at the original prepared locator')
    settings = (root / 'settings.json').read_bytes()
    ledger = {path.name: path.read_bytes() for path in (root / 'control-ledger').glob('*.json')}
    submissions = backend.submits
    with pytest.raises(q.IntegrityError):
        c.validate(c.workspace(root, 7))
    child = tmp_path / 'rejected-fork'
    with pytest.raises(q.IntegrityError):
        _copy_completed(root, 7, child)
    assert not child.exists() and root.exists()
    assert backend.submits == submissions
    assert (root / 'settings.json').read_bytes() == settings
    assert {path.name: path.read_bytes() for path in (root / 'control-ledger').glob('*.json')} == ledger
    assert (root / retained['snapshot']).stat().st_size == prepared['size_bytes']


def test_nested_raw_payload_uses_ancestor_owner_and_preserves_logical_identity(tmp_path):
    import shutil
    from scidiscovery.research_loop import execution_inputs as inputs, qualification as q
    from test_fork_closure import _copy_closure
    root = completed_source(tmp_path)
    raw = root / 'project/nodes/001/work/raw.bin'
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b'opaque original scientific bytes')
    material = dict(path='inputs/parents/001/artifacts/raw.bin', source=str(raw),
         size_bytes=raw.stat().st_size)
    nested = tmp_path / 'intermediate-origin'
    c.write(nested / 'settings.json', dict(goal_id='intermediate-source-context'))
    identity = dict(goal_id='intermediate-source-context', settings_ref=c.record_ref(nested / 'settings.json'))
    closure = fork_sources._Closure()
    closure.add(originals.node(root, 2), raw.relative_to(root).as_posix(), proof=material)
    context = originals.NodeSource(identity, 2, str(nested), nested)
    closure.add(context, 'settings.json')
    child = tmp_path / 'raw-reader'
    _copy_closure(root, closure, child)
    shutil.rmtree(nested)
    owner = originals.for_origin(child, identity, step=2)
    expected = dict(path=material['source'], size_bytes=material['size_bytes'])
    assert inputs.compiled_payload(child, material, source=owner) == expected
    exact_owner, path = originals.material_owner(owner, material)
    retained = originals.resolve_ref(exact_owner, dict(material, path=path))
    retained.chmod(0o600)
    retained.write_bytes(b'changed ancestor bytes')
    with pytest.raises(ValueError, match='Original size changed'):
        inputs.compiled_payload(child, material, source=owner)


def test_portable_legacy_local_inputs_use_exact_work_lineage_namespace(tmp_path, monkeypatch):
    """Pure retained Local contract, not a new execution or scientific verdict."""
    import copy
    import shutil
    from scidiscovery.research_loop import execution_inputs as inputs, experiments as e, qualification as q
    from test_fork_closure import _copy_closure
    root = completed_source(tmp_path)
    legacy = tmp_path / 'legacy-local-context'
    paths = []
    def write(path, value):
        actual = legacy / path
        c.write(actual, value)
        paths.append(actual)
        return q._ref(legacy, actual)
    def raw(path, value):
        actual = legacy / path
        actual.parent.mkdir(parents=True, exist_ok=True)
        actual.write_bytes(value)
        paths.append(actual)
        return dict(path=str(actual),  size_bytes=actual.stat().st_size)
    settings = write('settings.json', dict(goal_id='legacy-local-source', handoff_protocol=c.CURRENT))
    provenance = write('source-provenance/inputs.json', dict(version=1, step=2, edges=[], scope=q.SCOPE))
    job = dict(step=2, role='W', handoff_protocol=c.CURRENT, global_materials=[], direct_refs=[], material_refs=[],
        direct_materials=[], selected_inputs=[], source_provenance=provenance)
    job_ref = write('jobs/002.json', c.job_record(job))
    intent = write('native-evidence/002/native-intent.json', dict(task_ref=dict(path=str(legacy/job_ref['path']))))
    content = e.encoded(e.INPUT_SOURCES_CONTRACT)
    contract = dict(key=e.INPUT_SOURCES_KEY, kind='source_provenance', schema_id=e.INPUT_SOURCES_SCHEMA,
        payload_schema_version=1, media_type='application/json', content=content.decode(),
        size_bytes=len(content))
    interface = dict(public_capabilities=[contract])
    scope, attempt = dict(step=2, job_ref=dict(path=str(legacy/job_ref['path']))), dict(number=0)
    config = dict(scope=scope, native_attempt=attempt, execution_interface=interface)
    configuration = write('host-runtime/experiment-control/002/interfaces/000.json', config)
    directory = legacy / 'project/nodes/002/experiments/revision'
    authored = raw('project/nodes/002/experiments/revision/run.py', b'print("original author")\n')
    snapshot = legacy / 'project/nodes/002/work/lineage/calls/one'
    script = raw('project/nodes/002/work/lineage/calls/one/source.py', b'print("original author")\n')
    raw('project/nodes/002/work/lineage/calls/one/inputs/run.py', b'print("original author")\n')
    prepared = dict(local_path=script['path'], size_bytes=script['size_bytes'])
    view = dict(job=job_ref, native_intent=intent, configuration=configuration, prepared=prepared,
        files=[dict(name='run.py', origin=dict(kind='author', step=2))])
    accepted = dict(call_id='one', scope=scope, native_attempt=attempt, interface=interface,
        local_observation_version=1, prepared=prepared, experiment_directory=str(directory),
        cwd=str(snapshot / 'inputs'), entry='run.py', source_script=authored['path'], script=script,
        snapshot_files=[dict(authored, name='run.py')], input_sources=dict(contract=contract, view=view),
        config_ref=dict(path=str(legacy/configuration['path'])),
        config_record={key: value for key, value in config.items() if key != 'execution_interface'})
    identity = dict(goal_id='legacy-local-source', settings_ref=dict(path=str(legacy/settings['path'])))
    local = originals.NodeSource(identity, 2, str(legacy), legacy)
    closure = fork_sources._Closure()
    for path in paths:
        closure.add(local, path.relative_to(legacy).as_posix())
    child = tmp_path / 'legacy-local-reader'
    _copy_closure(root, closure, child)
    shutil.rmtree(legacy)
    owner = originals.for_origin(child, identity, step=2)
    def unexpected_live_lookup(*args, **kwargs):
        raise AssertionError('Portable Local validation attempted a live original job lookup')
    monkeypatch.setattr(inputs.experiment_client, 'record_directory', unexpected_live_lookup)
    assert inputs.validate_local_input_sources(child, job, accepted, source=owner) == []
    wrong = copy.deepcopy(accepted)
    wrong['cwd'] = str(legacy / 'host-runtime/experiment-control/002/records/calls/one/inputs')
    with pytest.raises(q.IntegrityError, match='snapshot inventory'):
        inputs.validate_local_input_sources(child, job, wrong, source=owner)
