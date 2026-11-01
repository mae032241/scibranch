"""Retained authority and recovery callers consume their current interfaces."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from scidiscovery import execution_security as security
from scidiscovery.research_loop import accepted_budget, contracts as c
from scidiscovery.research_loop import execution_authority as authority, execution_inputs
from scidiscovery.research_loop import execution_recovery, experiments as e, history
from scidiscovery.research_loop import managed_wait, originals, qualification as q


@pytest.mark.parametrize('inherited', [False, True])
def test_retained_authority_projects_exact_selected_material(tmp_path, monkeypatch, inherited):
    root = tmp_path.resolve()
    job = dict(step=2, role='W')
    job_ref = q._save(root, root/'jobs/002.json', job)
    record = root/'native-evidence/002'
    native_ref = q._save(root, record/'native-intent.json', dict(task_ref=q.control_reference(root, job_ref)))
    package = root/'project/nodes/001/artifacts/package.json'
    package.parent.mkdir(parents=True)
    package.write_bytes(b'{}')
    material = dict(path='inputs/parents/001/artifacts/package.json', source=str(package), size_bytes=2)
    producer_seal = q._save(root, root/'native-evidence/001/result-identity.json',
        dict(materials=[dict(material, file_stat=[1, 2, 2, 3])]))
    subject = dict(producer_step=1, producer_seal=producer_seal, material=material)
    call = 'original'
    scope = dict(step=2, job_ref=q.control_reference(root, job_ref))
    attempt = dict(number=0)
    interface = dict(executor='fixture')
    payload = dict(path=str(package), size_bytes=2)
    target = dict(configuration_files=[])
    review = dict(path='qualification/retained-review.json', size_bytes=1)
    binding = dict(scope=scope, native_attempt=attempt, interface=interface,
        subject=subject, review=review, payload_alias=payload)
    allocation = q._save(root, root/'execution-authority/calls'/call/'allocation.json',
        dict(call_id=call, native_intent=native_ref, binding=binding))
    request = dict(kind='execution_authorization', options=list(authority.OPTIONS),
        job=job_ref, call_id=call, native_intent=native_ref, subject=subject, review=review,
        payload=payload, target=target)
    request_id = security.request_digest(request)
    directory = root/'execution-authority/requests'/request_id
    request_ref = q._save(root, directory/'request.json', dict(request, nonce_hash='retained-nonce'))
    decision_ref = q._save(root, directory/'decision.json', dict(request=request_ref,
        nonce_hash='retained-nonce', selected_option=authority.OPTIONS[0]))
    authority_ref = q._save(root, directory/'authorization.json', dict(scope=scope,
        native_attempt=attempt, interface=interface, call_id=call, target=target,
        payload_alias=payload, allocation=allocation, decision=decision_ref, request=request_ref))
    config = dict(scope=scope, native_attempt=attempt, execution_interface=interface)
    c.write(record/'experiment-config.json', config)
    accepted = dict(call_id=call, scope=scope, native_attempt=attempt, interface=interface,
        execution_authority=dict(authority_ref, path=str(root/authority_ref['path'])),
        execution_target=target, prepared=dict(local_path=str(package), size_bytes=2), script=payload)
    monkeypatch.setattr(execution_inputs, '_native_record', lambda *args, **kwargs: record)
    monkeypatch.setattr(execution_inputs, 'compiled_payload', lambda *args, **kwargs: payload)
    monkeypatch.setattr(originals, 'node', lambda *args: pytest.fail('Projection reopened the selected producer'))
    source = None
    if inherited:
        source = SimpleNamespace(original_root=str(root), inherited=True)
        producer = SimpleNamespace(original_root=str(root), step=1)
        monkeypatch.setattr(originals, 'dependency', lambda *args, **kwargs: producer)
        monkeypatch.setattr(q, '_source_path', lambda owner, ref: root/Path(ref['path']))
    assert authority._accepted_input_subject(root, job, accepted, None, source=source) == dict(
        subject=subject, authority=authority_ref, request=request_ref,
        decision=decision_ref, allocation=allocation)


@pytest.mark.parametrize('status', ['known_success', 'known_failed', 'not_submitted', 'unknown'])
def test_sealed_recovery_accepts_only_changed_original_call(tmp_path, monkeypatch, status):
    root = tmp_path.resolve()
    job = dict(step=2, role='W')
    call = 'original'
    records = root/'records'
    directory = records/'calls'/call
    accepted_path = directory/'accepted.json'
    c.write(accepted_path, dict(call_id=call))
    c.write(directory/'execution-observation.json', dict(retained=True))
    old_row = dict(call_id=call, status='unknown', accepted=dict(snapshot=accepted_path.relative_to(root).as_posix()))
    c.write(root/'observed.json', dict(managed_calls=[old_row, dict(call_id='finished', status='known_success')]))
    seal = dict(observations=dict(path='observed.json'))
    current = dict(old_row, status=status)
    produced, validated, published = [], [], []
    monkeypatch.setattr(originals, 'inherited', lambda *args: False)
    monkeypatch.setattr(history, '_source', lambda *args: (job, None, seal, 'original-seal'))
    monkeypatch.setattr(managed_wait, '_config', lambda *args: (None, dict(record_dir=str(records))))
    monkeypatch.setattr(e, 'retirement', lambda *args: None)
    monkeypatch.setattr(e, '_cleanup_terminal_code_export', lambda *args: None)

    def observe(given_root, given_job, *, call_ids, context):
        produced.append((given_root, given_job, call_ids, context))
        return [current]

    def validate(given_root, given_job, rows, *, context):
        validated.append((given_root, given_job, rows, context))

    monkeypatch.setattr(history, '_observe_managed_calls', observe)
    monkeypatch.setattr(history, '_validate_managed', validate)
    monkeypatch.setattr(execution_recovery, 'validate', lambda *args: None)
    monkeypatch.setattr(accepted_budget, 'read', lambda *args: 'accepted-head')
    monkeypatch.setattr(execution_recovery, '_publish_fact', lambda *args: published.append(args))
    path = root/execution_recovery.DIRECTORY/'002'/f'{call}.json'
    if status == 'unknown':
        with pytest.raises(ValueError, match='terminal is unverified'):
            execution_recovery.create(root, 2)
        assert not path.exists() and published == []
    else:
        result = execution_recovery.create(root, 2)
        assert result['status'] == 'execution_recovered' and result['recovered'] == 1
        assert c.read(path) == dict(source_step=2, seal_ref='original-seal', old_row=old_row, row=current)
        assert len(published) == 1 and published[0][2:5] == (2, call, dict(path=path.relative_to(root).as_posix()))
    assert produced == [(root, job, [call], None)]
    assert validated == [(root, job, [current], None)]


def test_cov_configuration_passes_expanded_job_and_producer_sources(tmp_path, monkeypatch):
    from cov_execution import lineage
    from scidiscovery.research_loop import protocol
    root = tmp_path.resolve()
    retained = dict(step=2, role='W')
    c.write(root/'jobs/002.json', retained)
    expanded = dict(retained, direct_materials=[], selected_inputs=[])
    producer = object()
    produced = []
    monkeypatch.setattr(protocol, 'require_current', lambda *args: None)

    def read_job(path, *, sources):
        assert path == root/'jobs/002.json'
        sources[1] = producer
        produced.append(sources)
        return expanded

    class Validated(Exception):
        pass

    def validate(given_root, work, job, *, _sources):
        assert given_root == root and work == c.workspace(root, 2)
        assert job is expanded and _sources is produced[0]
        assert _sources == {1: producer}
        raise Validated()

    monkeypatch.setattr(c, 'read_job', read_job)
    monkeypatch.setattr(c, '_validate_inputs', validate)
    with pytest.raises(Validated):
        lineage.configure(root, 2, ['fixture'], root/'runs', root/'config.json')


def test_compiler_coverage_supplies_expanded_input_facts(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    compiler_provenance = q._save(root, root/'compiler-inputs.json', dict(edges=[]))
    compiler_job = dict(step=1, role='W', material_refs=[], selected_inputs=[],
        global_materials=[], direct_refs=[], source_provenance=compiler_provenance)
    compiler_job_ref = q._save(root, root/'jobs/001.json', compiler_job)
    compiler_ref = dict(path='native-evidence/002/compiled-implementations/original/compiler.json', size_bytes=1)
    material = dict(path='inputs/parents/002/artifacts/package.json', source=str(root/'package.json'), size_bytes=2)
    result_ref = dict(path=str(root/'native-evidence/002/result.json'))
    seal_ref = q._save(root, root/'native-evidence/002/result-identity.json', dict(result_ref=result_ref,
        compiled_implementations=[dict(material=material, compiler=compiler_ref)]))
    subject = dict(producer_step=2, producer_seal=seal_ref, material=material)
    provenance = q._save(root, root/'current-inputs.json', dict(edges=[dict(kind='producer', step=2, seal=seal_ref)]))
    job = dict(step=3, source_provenance=provenance, direct_materials=[], selected_inputs=[dict(
        origin_step=2, origin_result_ref=result_ref, material=material)])
    monkeypatch.setattr(execution_inputs, 'verify_compilation', lambda *args, **kwargs:
        dict(job=compiler_job_ref, inputs=[]))
    validated = []

    def validate(given_root, owner):
        assert given_root == root
        assert owner['direct_materials'] == []
        validated.append(owner['step'])

    monkeypatch.setattr(q, 'validate_inputs', validate)
    assert execution_inputs.execution_input_coverage(root, job, subject) == compiler_ref
    assert validated == [3, 1]


@pytest.mark.parametrize('facts_kind', ['owner', 'missing', 'another_task'])
def test_local_code_metadata_consumes_owner_input_facts(tmp_path, monkeypatch, facts_kind):
    from scidiscovery.operations.execution import LocalFileDescriptor
    from scidiscovery.research_loop import experiment_client
    from scidiscovery.research_loop.local_experiments import LocalExecution
    root = tmp_path.resolve()
    work = c.workspace(root, 2)
    work.mkdir(parents=True)
    source = root/'export/main.py'
    source.parent.mkdir()
    source.write_text('print(1)\n')
    material = dict(path='inputs/parents/001/artifacts/data.csv', source=str(root/'original.csv'), size_bytes=2)
    job = dict(step=2, role='W', worker_view_version=1, global_materials=[], direct_refs=[],
        direct_materials=[material], selected_inputs=[dict(material=material)])
    config = dict(run_argv=['fixture'], scope=dict(step=2),
        record_dir=str(root/'host-runtime/experiment-control/002/records'))
    adapter = LocalExecution.__new__(LocalExecution)
    adapter.config = config
    payload = LocalFileDescriptor(name='original', local_path=str(source), size_bytes=source.stat().st_size,
        media_type='text/x-python')
    facts = dict(root=root, work=work, job=job)
    if facts_kind == 'missing':
        facts = None
    elif facts_kind == 'another_task':
        facts = dict(facts, job=dict(job, step=3))

    def forbidden(*args, **kwargs):
        pytest.fail('Local metadata reopened owner-loaded scientific inputs')

    monkeypatch.setattr(c, 'read', forbidden)
    monkeypatch.setattr(c, 'read_job', forbidden)
    monkeypatch.setattr(experiment_client, 'load_client_facts', forbidden)
    monkeypatch.setattr(experiment_client, 'root_for_configuration', forbidden)
    options = dict(command_options=dict(source_script=str(work/'code/main.py')),
        _code_version=dict(version=1), client_facts=facts)
    if facts_kind != 'owner':
        with pytest.raises(e.IntegrityError, match='scientific'):
            adapter.acceptance_metadata(payload, **options)
    else:
        metadata = adapter.acceptance_metadata(payload, **options)
        assert metadata['script']['path'] == str(source)
        assert metadata['source_script'] == str(work/'code/main.py')
        assert metadata['cwd'] is None


def test_native_payload_callbacks_keep_expanded_compiler_job(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    data = root/'data.csv'
    data.write_bytes(b'1,2\n')
    material = dict(path='inputs/parents/001/artifacts/data.csv', source=str(data), size_bytes=4)
    compiler_job_ref = q._save(root, root/'jobs/002.json', dict(step=2, material_refs=[]))
    expanded = dict(step=2, direct_materials=[material], selected_inputs=[])
    compiler_ref = dict(path='native-evidence/002/compiled-implementations/original/compiler.json', size_bytes=1)
    package = dict(path='inputs/parents/002/artifacts/package.json')
    seal_ref = q._save(root, root/'native-evidence/002/result-identity.json', dict(
        compiled_implementations=[dict(material=package, compiler=compiler_ref)]))
    subject = dict(producer_seal=seal_ref, material=package)
    original = dict(producer_step=1, material_path=material['path'])
    declared = dict(semantic_name='train', original=original, size_bytes=4, media_type='text/csv')
    item = SimpleNamespace(model_dump=lambda *, mode: declared, media_type='text/csv')
    monkeypatch.setattr(authority, '_selected_subject', lambda *args: (subject, None, None))
    monkeypatch.setattr(execution_inputs, 'verify_compilation', lambda *args, **kwargs:
        dict(job=compiler_job_ref, inputs=[dict(declared, alias=material['path'])]))
    produced = []

    def read_job(path):
        assert path == root/'jobs/002.json'
        produced.append(path)
        return expanded

    def resolve(given_root, given_original, *, compiler_job, source=None):
        assert given_root == root and given_original == original
        assert compiler_job is expanded and compiler_job['direct_materials'] == [material]
        assert source is None
        return material

    monkeypatch.setattr(c, 'read_job', read_job)
    monkeypatch.setattr(execution_inputs, 'resolve_native_original', resolve)
    monkeypatch.setattr(originals, 'material_path', lambda *args: data)
    host = execution_inputs.NativeInputHost.__new__(execution_inputs.NativeInputHost)
    host.root, host.job = root, dict(step=3)
    metadata, open_input = host.for_payload(object(), [item])
    assert metadata(item) == (4, 'text/csv')
    with open_input(item) as source:
        assert source.read() == b'1,2\n'
    assert produced == [root/'jobs/002.json']


@pytest.fixture
def inherited_compiler_inputs(tmp_path):
    root = tmp_path/'fork'
    original_root = tmp_path/'original'
    identity = dict(goal_id='a'*32, settings_ref=dict(path=str(original_root/'settings.json')))
    storage = root/originals.FORK_DIRECTORY
    entries = []

    def transported(relative, value):
        destination = storage/'inherited'/identity['goal_id']/relative
        c.write(destination, value)
        size = destination.stat().st_size
        entries.append(dict(origin_identity=identity, path=relative,
            storage_path=destination.relative_to(storage).as_posix(), size_bytes=size,
            visibility='host_proof'))
        return dict(path=relative, size_bytes=size)

    data = original_root/'project/nodes/001/artifacts/data.csv'
    data.parent.mkdir(parents=True)
    data.write_bytes(b'1,2\n')
    material = dict(path='inputs/parents/001/artifacts/data.csv', source=str(data), size_bytes=4)
    relative = data.relative_to(original_root).as_posix()
    entries.append(dict(origin_identity=identity, path=relative,
        storage_path='inherited/'+identity['goal_id']+'/'+relative,
        size_bytes=4, visibility='scientific_input'))
    transported('settings.json', dict(goal_id=identity['goal_id']))
    transported('jobs/001.json', dict(step=1, role='H', material_refs=[]))
    raw_ref = transported('native-evidence/001/science-output.json', dict(scientific_result='Exact input'))
    result_ref = dict(path=str(original_root/'native-evidence/001/result.json'))
    producer_seal = transported('native-evidence/001/result-identity.json', dict(
        job_ref=dict(path=str(original_root/'jobs/001.json')),
        raw_ref=dict(raw_ref, path=str(original_root/raw_ref['path'])),
        result_ref=result_ref, materials=[material]))
    provenance = transported('source-provenance/compiler-inputs.json', dict(
        version=1, step=2, scope=q.SCOPE, edges=[dict(kind='producer', step=1,
            seal=producer_seal, missing_reason='legacy_producer_without_source_proof')]))
    list_ref = dict(origin_identity=identity, step=1,
        path=str(original_root/'native-evidence/001/result-identity.json'))
    selection = dict(label='Exact input', origin_step=1, origin_result_ref=result_ref,
        material_ref=dict(list_ref=list_ref, id='H001-I001'))
    job_ref = transported('jobs/002.json', dict(step=2, role='W',
        material_refs=[dict(list_ref=list_ref, ids=['H001-I001'])],
        selected_inputs=[selection], global_materials=[],
        direct_refs=[dict(path='inputs/parents/001/conclusion.json')], source_provenance=provenance))
    original = dict(instance_id=identity['goal_id'], producer_step=1,
        producer_seal=producer_seal, material_path=material['path'], size_bytes=4)
    declared = dict(semantic_name='train', original=original, size_bytes=4, media_type='text/csv')
    compiler = dict(job=job_ref, inputs=[dict(declared, alias=material['path'])])
    compiler_ref = transported('native-evidence/002/compiled-implementations/original/compiler.json', compiler)
    compiler_ref['origin_identity'] = identity
    c.write(root/originals.MANIFEST, dict(version=2, kind='scientific_fork', entries=entries,
        origins={identity['goal_id']: dict(identity=identity, original_root=str(original_root))},
        prefix_entries=[dict(step=step, origin_identity=identity) for step in (1, 2)]))
    package = dict(path='inputs/parents/002/artifacts/package.json', source=str(original_root/'package.json'), size_bytes=2)
    seal_ref = q._save(root, root/'native-evidence/002/result-identity.json', dict(result_ref=result_ref,
        compiled_implementations=[dict(material=package, compiler=compiler_ref)]))
    subject = dict(producer_step=2, producer_seal=seal_ref, material=package)
    current_provenance = q._save(root, root/'current-inputs.json', dict(edges=[
        dict(kind='producer', step=2, seal=seal_ref)]))
    job = dict(step=3, source_provenance=current_provenance, direct_materials=[],
        selected_inputs=[dict(origin_step=2, origin_result_ref=result_ref, material=package)])
    return SimpleNamespace(root=root, original_root=original_root, identity=identity,
        compiler=compiler, compiler_ref=compiler_ref, job_ref=job_ref, job=job,
        subject=subject, declared=declared, material=material)


@pytest.mark.parametrize('consumer', ['coverage', 'native_callbacks'])
def test_inherited_compiler_inputs_keep_original_namespace(inherited_compiler_inputs, monkeypatch, consumer):
    scene = inherited_compiler_inputs
    transported_job = originals.resolve_root_ref(scene.root, dict(scene.job_ref, origin_identity=scene.identity))
    assert transported_job != scene.original_root/scene.job_ref['path']
    # Archive parents cannot stand in for the consuming fork's original source.
    with pytest.raises((OSError, ValueError)):
        c.read_job(transported_job)
    monkeypatch.setattr(execution_inputs, 'verify_compilation', lambda *args, **kwargs: scene.compiler)
    if consumer == 'coverage':
        def current_inputs(root, job):
            assert root == scene.root and job is scene.job
        monkeypatch.setattr(q, 'validate_inputs', current_inputs)
        assert execution_inputs.execution_input_coverage(scene.root, scene.job, scene.subject) == scene.compiler_ref
    else:
        monkeypatch.setattr(authority, '_selected_subject', lambda *args: (scene.subject, None, None))
        item = SimpleNamespace(model_dump=lambda *, mode: scene.declared, media_type='text/csv')
        host = execution_inputs.NativeInputHost.__new__(execution_inputs.NativeInputHost)
        host.root, host.job = scene.root, scene.job
        metadata, open_input = host.for_payload(object(), [item])
        assert metadata(item) == (4, 'text/csv')
        with open_input(item) as source:
            assert source.read() == b'1,2\n'
